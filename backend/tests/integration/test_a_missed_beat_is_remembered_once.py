"""一次停摆只记一条，而且只在真的停摆时记。

影子期（第一段）判到停摆只记「本该补跳」、不真起轮，所以这一条运行记录就是全部产出：
记重了，查记录的人会以为这一拍断了两回；记漏了，这一拍断了没人知道。记录落在运行记
录里、不进对话 —— 观察者不该把被观察的座位叫起来，也不该混进人读的聊天。
"""

import functools
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.domain.block.models import AuthorType, Block, BlockKind
from app.domain.run_record.models import RunRecord
from app.domain.wake_watch.beats import Beat
from app.domain.wake_watch.observe import watch
from tests.integration.conftest import room_text
from tests.integration.test_same_handle_note_and_timed_delivery import (
    _blocks,
    _project,
    _room,
)

MARKER = "【20 分钟巡检·定时器】"
AT = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)


def _beat(room: str) -> Beat:
    """这一拍指到测试刚建的房间里。

    判据比的是「这一拍该来的时刻」和「它上一次真的来的时刻」，房间里坐着谁、叫什么
    名字都不参与，所以指到哪条对话都一样。
    """
    return Beat(
        name="timer",
        label="wakeWatchBeatTimer",
        conversation_id=uuid.UUID(room),
        marker=MARKER,
    )


def _say(client, room: str, project: str, content: str, at: datetime) -> None:
    """这一拍的一拍落在房间里：一条带标记的消息，时间是它来的时刻。"""

    async def write() -> None:
        async with client.test_factory() as session:
            session.add(
                Block(
                    project_id=uuid.UUID(project),
                    conversation_id=uuid.UUID(room),
                    author="cheese-a",
                    author_type=AuthorType.participant,
                    kind=BlockKind.message,
                    content=content,
                    created_at=at,
                    updated_at=at,
                )
            )
            await session.commit()

    client.portal.call(write)


def _look(client, beat: Beat, now: datetime) -> dict[str, int]:
    """巡检看一遍这两拍（这里只有一拍）。"""
    return client.portal.call(
        functools.partial(watch, client.test_request_factory, now=now, watched=(beat,))
    )


def _records(client, room: str) -> list[dict]:
    """这个房间留下来几条「本该补跳」。"""

    async def read() -> list[dict]:
        async with client.test_factory() as session:
            rows = await session.scalars(
                select(RunRecord).where(
                    RunRecord.conversation_id == uuid.UUID(room),
                    RunRecord.kind == "wake_missed",
                )
            )
            return [
                {
                    "id": row.id,
                    "severity": row.severity,
                    "content": row.content,
                    "meta": dict(row.meta or {}),
                }
                for row in rows
            ]

    return client.portal.call(read)


def _room_with_one_beat(client) -> tuple[str, str, Beat]:
    project = _project(client, "停摆")
    room = _room(client, project, "总调度")
    _say(client, room, project, f"@芝士人 {MARKER}", AT)
    return project, room, _beat(room)


def test_a_stalled_beat_leaves_one_record(client):
    _, room, beat = _room_with_one_beat(client)

    assert _look(client, beat, AT + timedelta(minutes=61)) == {"stalled": 1}

    (kept,) = _records(client, room)
    assert kept["severity"] == "warn"
    assert "【该发没发】" in kept["content"]
    assert kept["meta"]["event_type"] == "wake_missed"
    # 这是平台自己的观察，不是某个座位做的一步。
    assert kept["meta"]["who"] == "platform"
    assert kept["meta"]["beat"] == "timer"
    assert kept["meta"]["stalled_minutes"] == 61
    assert kept["meta"]["last_beat_at"] == "2026-10-13T12:00:00Z"
    # 记录带着句子键：换个语言读回来是同一句话，不是那段中文。
    said = kept["meta"]["i18n"]["content"]
    assert said["key"] == "wakeWatchStalled"
    assert said["params"]["minutes"] == 61
    assert said["params"]["beat"]["key"] == "wakeWatchBeatTimer"


def test_the_observation_stays_out_of_the_room(client):
    """观察者说的话不该落进人读的聊天：它是平台的观察，进运行记录。"""
    _, room, beat = _room_with_one_beat(client)
    _look(client, beat, AT + timedelta(minutes=61))

    assert "该发没发" not in room_text(_blocks(client, room))


def test_the_same_stall_is_not_recorded_again(client):
    """巡检每五分钟看一遍，同一次停摆得一直只算一条。"""
    _, room, beat = _room_with_one_beat(client)
    assert _look(client, beat, AT + timedelta(minutes=61)) == {"stalled": 1}

    assert _look(client, beat, AT + timedelta(minutes=95)) == {}
    assert len(_records(client, room)) == 1


def test_a_beat_that_came_back_and_stopped_again_is_a_new_stall(client):
    project, room, beat = _room_with_one_beat(client)
    assert _look(client, beat, AT + timedelta(minutes=61)) == {"stalled": 1}

    # 又来了一拍，这一拍活着。
    _say(client, room, project, f"@芝士人 {MARKER}", AT + timedelta(minutes=70))
    assert _look(client, beat, AT + timedelta(minutes=80)) == {}
    assert len(_records(client, room)) == 1

    # 从那以后又静满了 60 分钟：这是新的一次，不是上一次。
    assert _look(client, beat, AT + timedelta(minutes=131)) == {"stalled": 1}
    two = _records(client, room)
    assert len(two) == 2
    assert len({row["id"] for row in two}) == 2


def test_a_beat_that_has_not_arrived_yet_is_not_reported(client):
    """房间建好了、这一拍一次都没来过：分不出「该来没来」和「还没起过头」，不报。"""
    project = _project(client, "没来过")
    room = _room(client, project, "总调度")

    assert _look(client, _beat(room), AT) == {}
    assert _records(client, room) == []


def test_the_live_heartbeat_is_the_one_that_counts(client):
    """心跳那条消息是原地改写的，废弃的那几条还躺在同一个房间里。

    时间只能从正文里读，而且只认开头就是那串字的那一条 —— 两条都做到，才判得出
    「心跳一直活着」。少哪一条，活着的这一拍都会被报成静了一整天。
    """
    project = _project(client, "心跳")
    room = _room(client, project, "看门狗")
    posted = AT - timedelta(days=1)
    _say(
        client,
        room,
        project,
        "（旧心跳块，已废弃）看门狗心跳 2026-10-12T12:00:00Z",
        posted,
    )
    _say(client, room, project, "看门狗心跳 2026-10-13T12:00:00Z", posted)
    beat = Beat(
        name="watchdog",
        label="wakeWatchBeatWatchdog",
        conversation_id=uuid.UUID(room),
        marker="看门狗心跳 ",
        marker_first=True,
        stamp_in_content=True,
    )

    assert _look(client, beat, AT + timedelta(minutes=59)) == {}
    assert _records(client, room) == []
