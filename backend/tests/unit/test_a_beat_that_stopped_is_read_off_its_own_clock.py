"""一拍停了没有，只能拿它自己说的时间比。

两拍的「上一次是什么时候」来源不同：定时器每一拍是一条新消息，消息自己的时间就是
这一拍；看门狗的心跳是同一条消息被原地改写，消息上的时间冻在它第一次发的那天
（2026-10-13 实测：块上是 10-12T16:20，正文里写着 10-13T17:01）。所以判据读的是这
一拍**自己说的**时刻，不是块的 `created_at` —— 拿块时间去看心跳，一直活着的那一拍
也会被报成静了一天。

这里只跑纯判据，不碰库：判据要能在没有数据库的地方，拿历史数据直接验一遍。
"""

import uuid
from datetime import datetime, timedelta

import pytest

from app.domain.wake_watch.beats import (
    GRACE,
    PERIOD,
    Beat,
    read_stamp,
    stall,
    stall_record_id,
    stamp_text,
)

#: 两拍在哪条对话里不影响判据，测试里给同一个房间就行。
ROOM = uuid.UUID("4f1d0b6c-9a3e-4c8b-8f2d-6b5a1c7e9d04")

TIMER = Beat(
    name="timer",
    label="wakeWatchBeatTimer",
    conversation_id=ROOM,
    marker="【20 分钟巡检·定时器】",
)
HEARTBEAT = Beat(
    name="watchdog",
    label="wakeWatchBeatWatchdog",
    conversation_id=ROOM,
    marker="看门狗心跳 ",
    marker_first=True,
    stamp_in_content=True,
)


def _at(text: str) -> datetime:
    return datetime.fromisoformat(text)


#: 心跳那条消息被原地改写之前，块上的时间。
FROZEN = _at("2026-10-12T16:20:00+00:00")
LAST = _at("2026-10-13T12:00:00+00:00")


def test_the_line_is_the_two_numbers_the_dispatcher_set() -> None:
    """周期 20 分、宽限 40 分（2026-10-13 总调度定：连续 60 分钟没有新的一拍判停摆）。

    这两个数是判据本身，改动等于改判据，所以钉死在这里，不跟着实现走。
    """
    assert PERIOD == timedelta(minutes=20)
    assert GRACE == timedelta(minutes=40)
    assert TIMER.stall_after == timedelta(minutes=60)


@pytest.mark.parametrize(
    ("quiet", "stalled"),
    [
        (timedelta(minutes=20), False),
        (timedelta(minutes=59, seconds=59), False),
        (timedelta(minutes=60), True),
        (timedelta(minutes=60, seconds=1), True),
    ],
)
def test_the_line_itself_counts_as_stalled(quiet, stalled) -> None:
    assert (stall(TIMER, LAST + quiet, LAST) is not None) is stalled


def test_a_long_quiet_spell_says_how_long() -> None:
    assert stall(TIMER, LAST + timedelta(hours=5), LAST) == timedelta(hours=5)


def test_a_beat_never_seen_is_not_called_stalled() -> None:
    """从没见过这一拍时，分不出「该来没来」和「还没起过头」，宁可不报。"""
    assert stall(TIMER, LAST, None) is None


def test_the_heartbeat_is_read_from_what_it_says_not_from_its_message() -> None:
    said = read_stamp(HEARTBEAT, "看门狗心跳 2026-10-13T17:01:34Z", FROZEN)

    assert said == _at("2026-10-13T17:01:34+00:00")
    assert said != FROZEN


def test_a_retired_heartbeat_block_is_not_the_live_one() -> None:
    """旧的那几条心跳块还留在同一个房间里，前面带一句「（旧心跳块，已废弃）」。"""
    retired = "（旧心跳块，已废弃）看门狗心跳 2026-10-12T16:20:00Z"

    assert read_stamp(HEARTBEAT, retired, FROZEN) is None


def test_the_timer_s_time_is_the_message_it_arrived_on() -> None:
    at = _at("2026-10-13T17:00:26+00:00")

    assert read_stamp(TIMER, "@芝士人 【20 分钟巡检·定时器】\n今天没事", at) == at


def test_a_message_without_the_marker_is_not_a_beat() -> None:
    assert read_stamp(TIMER, "今天没事", LAST) is None


def test_a_stamp_that_cannot_be_read_is_none_not_a_guess() -> None:
    assert read_stamp(HEARTBEAT, "看门狗心跳 待命", FROZEN) is None
    assert read_stamp(HEARTBEAT, "看门狗心跳", FROZEN) is None


def test_a_stamp_without_a_zone_is_read_as_utc() -> None:
    """正文里那一串要么带 Z 要么带偏移；真碰上没有的，按 UTC 读，不按本机时区。"""
    assert read_stamp(HEARTBEAT, "看门狗心跳 2026-10-13T17:01:34", FROZEN) == _at(
        "2026-10-13T17:01:34+00:00"
    )


def test_a_stamp_is_written_as_the_logs_write_it() -> None:
    assert stamp_text(_at("2026-10-13T17:01:34+08:00")) == "2026-10-13T09:01:34Z"


def test_one_stall_is_one_id_however_often_it_is_looked_at() -> None:
    """巡检每五分钟看一遍，同一次停摆每一遍读到的最后一拍都是同一个，于是算出同一个
    记录 id —— 记过没记过不用另建一张表。"""
    assert stall_record_id(TIMER, LAST) == stall_record_id(TIMER, LAST)


def test_a_new_last_beat_is_a_new_stall() -> None:
    assert stall_record_id(TIMER, LAST) != stall_record_id(
        TIMER, LAST + timedelta(minutes=20)
    )


def test_the_two_beats_never_share_an_id() -> None:
    assert stall_record_id(TIMER, LAST) != stall_record_id(HEARTBEAT, LAST)


def test_a_duplicate_summons_does_not_move_the_last_beat_backwards() -> None:
    """#1343 上实测过几秒里连着点名两次的突发（15:40:04 / 15:40:24 / 15:41:08）。

    最后一拍取的是最新那一条，被点名点了几遍不影响它；判据拿它和现在比，于是突发
    之后照样是 20 分钟一拍。
    """
    burst = [
        _at("2026-10-13T15:40:04+00:00"),
        _at("2026-10-13T15:40:24+00:00"),
        _at("2026-10-13T15:41:08+00:00"),
    ]

    assert max(burst) == burst[-1]
    assert stall(TIMER, burst[-1] + timedelta(minutes=59), max(burst)) is None
    assert stall(TIMER, burst[-1] + timedelta(minutes=61), max(burst)) is not None
