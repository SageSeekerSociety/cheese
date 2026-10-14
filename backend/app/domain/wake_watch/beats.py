"""盯哪两拍、一拍静多久算停摆 —— 判据这一半，不碰数据库。

判的不是「库里有没有某一行」，是「该来的那一拍对不上最后一次真的来的那一拍」。
这种缺失不会留下表示缺席的行，只会在该出现的地方少一次准时出现，所以判据只能由
「该来的时刻」和「最后一拍的时刻」比出来。

两拍的时间戳来源不一样，这是这个模块存在的理由：

- 定时器每一拍新发一条消息，消息自己的时间就是这一拍的时间。
- 心跳是同一条消息被原地改写的，消息上的时间冻在它第一次发的那天（2026-10-13
  实测：块上的时间是 10-12T16:20，正文里写着 10-13T17:01），真正的时间只能从
  正文里读。

只依赖标准库，所以判据可以拿历史数据在没有环境的地方直接验一遍。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final

#: 一拍该多久来一次。两处都是 20 分钟（2026-10-13 实测：#1343 的定时器在稳态下
#: 106 拍里绝大多数是 20.01 分，心跳也是 20 分钟一次）。
PERIOD: Final = timedelta(minutes=20)
#: 宽限：连着 PERIOD + GRACE（60 分钟）没有新的一拍才判停摆。
#: 40 分不是拍的节奏，是给「晚到」留的余量 —— 换工作机、沙箱被回收后重挂、平台
#: 排队都会让一拍晚几分钟（同期实测里，没有被判停摆的最长一次是 57.26 分）。
GRACE: Final = timedelta(minutes=40)


@dataclass(frozen=True)
class Beat:
    """该按固定周期留下的一拍。"""

    #: 码。进运行记录的 `meta.beat`，也是停摆代号的前半段。
    name: str
    #: 给人看的称呼，`wakeWatchBeat*` 那组句子的键。
    label: str
    #: 这一拍留在哪条对话里。
    conversation_id: uuid.UUID
    #: 正文里认出这一拍的那串字。
    marker: str
    #: 这串字必须出现在正文开头。心跳的旧块前面带一句「（旧心跳块，已废弃）」，
    #: 不这么认就会把废弃的那两条也当成活的。
    marker_first: bool = False
    #: 时间戳写在正文里，不是块自己的 `created_at`。
    stamp_in_content: bool = False
    period: timedelta = PERIOD
    grace: timedelta = GRACE

    @property
    def stall_after(self) -> timedelta:
        """最后一拍之后静满这么久，就是停摆。"""
        return self.period + self.grace


#: 总调度的定时器：每 20 分钟往 #1343 发一条点名消息（正文里是 `@座位`
#: 加这个标记）。它不再发新消息，就是这一拍停了。
TIMER: Final = Beat(
    name="timer",
    label="wakeWatchBeatTimer",
    conversation_id=uuid.UUID("998a4d26-a7d7-4313-b3f5-b22304182d5d"),
    marker="【20 分钟巡检·定时器】",
)

#: 看门狗（#1421）的心跳：同一条消息被原地改写，时间写在正文里。
HEARTBEAT: Final = Beat(
    name="watchdog",
    label="wakeWatchBeatWatchdog",
    conversation_id=uuid.UUID("b0b58c35-0b14-4fd8-a361-86f41a9ce466"),
    marker="看门狗心跳 ",
    marker_first=True,
    stamp_in_content=True,
)

#: 盯着的那两拍。#1343 这一条线上的一对：定时器管「该巡检了」，心跳管「看门狗
#: 还活着」。2026-10-13 总调度定的范围就是这一对，不铺开到别的长跑座位。
WATCHED: Final = (TIMER, HEARTBEAT)


@dataclass(frozen=True)
class Ticking:
    """某一拍最近一次真的来了。"""

    #: 这一拍自己说的时间。
    at: datetime
    #: 它落在哪条消息上，那条消息自己的时间 —— 心跳那条被原地改写过，
    #: 所以它通常远早于 `at`。
    block_at: datetime


def read_stamp(beat: Beat, content: str, block_at: datetime) -> datetime | None:
    """这条消息是这一拍吗；是的话它说自己是什么时候。

    两种来源在这里收敛：正文里写的（心跳）读正文，其余的用消息自己的时间。正文
    里那串字读不出来就返回 None —— 返回不了好过返回一个错的时刻。
    """
    if beat.marker_first:
        if not content.startswith(beat.marker):
            return None
    elif beat.marker not in content:
        return None
    if not beat.stamp_in_content:
        return block_at
    said = content.split(beat.marker, 1)[1].strip()
    try:
        at = datetime.fromisoformat(said.replace("Z", "+00:00"))
    except ValueError:
        return None
    return at if at.tzinfo else at.replace(tzinfo=UTC)


def stall(beat: Beat, now: datetime, last: datetime | None) -> timedelta | None:
    """这一拍静了多久 —— 判为停摆时返回从最后一拍到现在，否则 None。

    从来没见过这一拍（None）不算停摆：分不出「该来没来」和「还没起过头」，宁可
    不报。
    """
    if last is None:
        return None
    since = now - last
    return since if since >= beat.stall_after else None


def stamp_text(at: datetime) -> str:
    """一个时刻，写成能对上日志和正文的样子。"""
    return at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def stall_window(beat: Beat, last: datetime) -> str:
    """一次停摆的代号：哪一拍、停在哪一刻。

    同一次停摆，每一轮读到的最后一拍都是同一个，于是算出同一个代号：巡检跑多少遍
    也只算一次，进程重启、换机器都不影响。它恢复之后下一次再停，最后一拍变了，
    那才是新的一次。代号也是运行记录 id 的来源，去重不查表。
    """
    return f"{beat.name}@{stamp_text(last)}"


#: 停摆记录 id 的命名空间。「同一次停摆只记一条」由 id 本身保证。
_NAMESPACE: Final = uuid.UUID("b0a3f9f0-1c6e-4b8a-9c1d-2f5e7a4d6c31")


def stall_record_id(beat: Beat, last: datetime) -> uuid.UUID:
    """这一次停摆该记成哪一条运行记录，由代号算出来。"""
    return uuid.uuid5(_NAMESPACE, stall_window(beat, last))
