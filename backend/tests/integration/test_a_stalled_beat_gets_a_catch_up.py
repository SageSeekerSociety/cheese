"""判到停摆之后，那一跳真的补到座位上（结论 11、12、17；不变量 I12）。

平台这一侧没有起一轮的路径，所以补跳是点名：给总调度那条线（#1343）上的座位记一条
投递，剩下的交给现有的投递派发（每 30 秒那一拍）。这几条测的是那一步真的会走到 ——
只记一条运行记录、或者记一条永远送不出去的投递，都等于没补。
"""

import functools
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.domain.agent.platform_notices import EVENT_WAKE_MISSED
from app.domain.block.models import AuthorType, Block, BlockKind
from app.domain.delivery.models import Delivery, NativeInput
from app.domain.delivery.timer import deliver_due
from app.domain.room_task.models import Task, TaskStatus
from app.domain.run_record.models import RunRecord
from app.domain.topic.models import Topic
from app.domain.wake_watch import catchup
from app.domain.wake_watch.beats import Beat, stall_record_id
from app.domain.wake_watch.observe import watch
from tests.integration.test_same_handle_note_and_timed_delivery import (
    _project,
    _room,
    _seat,
)

MARKER = "【20 分钟巡检·定时器】"
AT = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)
STALLED = AT + timedelta(minutes=61)


class _Recorder:
    """投递派发那一侧：记下每一轮点到谁头上了。"""

    def __init__(self):
        self.attempts = []

    def submit(self, chat, topic_id, **kwargs):
        self.attempts.append((kwargs["delivery_id"], topic_id, kwargs["content"]))


class _Line:
    """总调度那条线，换成一个测试房间。

    补跳按 `catchup.LINE` 找那条线，而它是一条真的任务对话（任务的 id 就是它自己那
    条对话，工作就跑在那段对话上）。指到测试建的这条上，就测得到点名的全过程，不碰
    真的 #1343。
    """

    def __init__(self, client, task_id: uuid.UUID, line: dict):
        self.client = client
        self.task_id = task_id
        self.project = line["project"]
        self.room = line["room"]
        self.seat = line["seat"]

    @property
    def id(self) -> str:
        return str(self.task_id)

    @property
    def beat(self) -> Beat:
        # 这一拍指到那条线自己的对话上：判据只看「这串字是不是出现在这条对话
        # 里」，房间里坐着谁、叫什么名字都不参与。
        return Beat(
            name="timer",
            label="wakeWatchBeatTimer",
            conversation_id=self.task_id,
            marker=MARKER,
        )

    def say(self, content: str, at: datetime) -> None:
        """这一拍的一拍落在这条线上：一条带标记的消息，时间是它来的时刻。"""

        async def write() -> None:
            async with self.client.test_factory() as session:
                session.add(
                    Block(
                        project_id=uuid.UUID(self.project),
                        conversation_id=self.task_id,
                        author="cheese-a",
                        author_type=AuthorType.participant,
                        kind=BlockKind.message,
                        content=content,
                        created_at=at,
                        updated_at=at,
                    )
                )
                await session.commit()

        self.client.portal.call(write)

    def look(self, now: datetime) -> dict[str, int]:
        """巡检看一遍，补跳接着判据一起跑（和线上那一份同一个组合）。"""
        return self.client.portal.call(
            functools.partial(
                watch,
                self.client.test_request_factory,
                now=now,
                watched=(self.beat,),
                summon=functools.partial(
                    catchup.summon, self.client.test_request_factory
                ),
            )
        )

    def judge_only(self, now: datetime) -> dict[str, int]:
        """只看不补：影子期那一份（补跳关着的时候走的正是这条）。"""
        return self.client.portal.call(
            functools.partial(
                watch, self.client.test_request_factory, now=now, watched=(self.beat,)
            )
        )

    def deliveries(self) -> list[Delivery]:
        async def read() -> list[Delivery]:
            async with self.client.test_factory() as session:
                return list(
                    await session.scalars(
                        select(Delivery).where(Delivery.conversation_id == self.task_id)
                    )
                )

        return self.client.portal.call(read)

    def record(self) -> dict:
        async def read() -> dict:
            async with self.client.test_factory() as session:
                row = await session.get(RunRecord, stall_record_id(self.beat, AT))
                return dict(row.meta or {})

        return self.client.portal.call(read)


@pytest.fixture
def line(client, monkeypatch) -> _Line:
    project = _project(client, "叫醒链")
    room = _room(client, project, "总调度")
    task_id = uuid.uuid4()
    seat = _seat(client, room)

    async def seed() -> None:
        async with client.test_factory() as session:
            session.add(
                Task(
                    id=task_id,
                    project_id=uuid.UUID(project),
                    room_id=uuid.UUID(room),
                    title="总调度",
                )
            )
            await session.commit()

    client.portal.call(seed)
    monkeypatch.setattr(catchup, "LINE", task_id)
    return _Line(
        client,
        task_id,
        {"project": project, "room": room, "seat": seat},
    )


def _stalled(line: _Line) -> None:
    """那条线上最后一拍停在 AT，从此再没有新的一拍。"""
    line.say(f"@芝士人 {MARKER}", AT)


def test_a_stalled_beat_is_handed_to_the_seat_on_that_line(client, line):
    """静满 60 分钟：那条线上的座位收到一条投递，第一行就是那句补跳。"""
    _stalled(line)

    assert line.look(STALLED) == {"stalled": 1}

    (delivery,) = line.deliveries()
    assert delivery.recipient_handle == line.seat
    assert delivery.state == "pending"
    text = delivery.payload["content"]
    assert text.startswith("【补跳】平台侧观察者：")
    assert "61" in text.splitlines()[0]
    # 判停摆的依据跟着一起送过去：座位看到的和判的人看到的是同一段话。
    assert "2026-10-13T12:00:00Z" in text
    # 重挂步骤本身不抄进来，只指到资料库那份源。
    assert "总调度看门狗：脚本与重挂步骤" in text


def test_the_catch_up_carries_the_dedup_key_of_its_stall(client, line):
    """去重键按停摆窗口算：巡检看多少遍，账上只有一条。"""
    _stalled(line)
    window = stall_record_id(line.beat, AT)

    line.look(STALLED)
    line.look(STALLED + timedelta(minutes=20))

    (delivery,) = line.deliveries()
    assert delivery.event_id == window
    assert delivery.dedup_key == f"{window}:{line.seat}"


def test_the_record_names_who_was_called(client, line):
    """运行记录里带上这一遍点了谁，事后对得上那条线上的那一轮。"""
    _stalled(line)
    line.look(STALLED)

    assert line.record()["catch_up_seat"] == line.seat


def test_the_delivery_carries_the_missed_beat_event(client, line):
    """投递上记的是同一件事：这一拍该发没发。

    换一套事件码会让「这一拍断过几次」在账本上分成两处数。
    """
    _stalled(line)
    line.look(STALLED)

    (delivery,) = line.deliveries()
    assert delivery.payload["eventType"] == EVENT_WAKE_MISSED


def test_the_catch_up_really_starts_a_turn(client, line):
    """记下的那条投递被现有的投递派发捡起来，点名声落在座位上。

    这一条是补跳的全部意义所在：判得再准，账上那一条送不出去就等于没补。
    """
    _stalled(line)
    line.look(STALLED)

    recorder = _Recorder()
    client.portal.call(
        functools.partial(
            deliver_due, client.test_request_factory, chat=object(), runner=recorder
        )
    )

    [(delivery_id, topic_id, text)] = recorder.attempts
    # 一轮跑在那条线自己的对话上：落到房间上会跑进另一条线，叫起来的还是同一个座
    # 位，但它手里的上下文不对。
    assert topic_id == line.task_id
    assert text.startswith("【补跳】平台侧观察者：")

    async def read() -> Delivery:
        async with client.test_factory() as session:
            return await session.get(Delivery, delivery_id)

    # 那一条已经被认领走了：这一轮不是「记下等着」，是真的在起。
    claimed = client.portal.call(read)
    assert claimed.attempts == 1
    assert claimed.attempt_id is not None


def test_a_closed_line_is_not_called(client, line):
    """那条线已经关了：不补，也不在它上面留一条注定失败的投递。"""
    _stalled(line)

    async def close() -> None:
        async with client.test_factory() as session:
            task = await session.get(Task, line.task_id)
            task.status = TaskStatus.closed
            await session.commit()

    client.portal.call(close)

    # 判照旧：线关了不影响「这一拍停了」这个事实。
    assert line.look(STALLED) == {"stalled": 1}
    assert line.deliveries() == []


def test_a_seat_that_is_still_working_is_left_alone(client, line):
    """座位上还有没处理完的输入：这一遍不补，等它空出来再看。

    硬塞进去的那一条会被入场闸挂住，一路重试到放弃，最后在那条线上留一行「没能起
    来」。停摆还在，判的还是同一次，等下一遍不损失什么。
    """
    _stalled(line)

    async def busy() -> None:
        async with client.test_factory() as session:
            session.add(
                NativeInput(
                    project_id=uuid.UUID(line.project),
                    conversation_id=line.task_id,
                    recipient_handle=line.seat,
                    harness="claude",
                    native_session_id="s",
                    input_id=uuid.uuid4(),
                    work_id=uuid.uuid4(),
                    registered_at=AT,
                )
            )
            await session.commit()

    client.portal.call(busy)

    assert line.look(STALLED) == {"stalled": 1}
    assert line.deliveries() == []


def test_the_judgement_still_records_when_the_catch_up_is_off(client, line):
    """补跳关着就退回影子期：判和记一个字不变，只是不点名。"""
    _stalled(line)

    assert line.judge_only(STALLED) == {"stalled": 1}
    assert line.deliveries() == []
    assert "catch_up_seat" not in line.record()


def test_the_catch_up_points_at_the_lines_own_conversation(client, line):
    """补跳落在那条线自己的对话里，不是它所在的房间。"""
    _stalled(line)

    async def room_of_the_line() -> uuid.UUID:
        async with client.test_factory() as session:
            task = await session.get(Task, line.task_id)
            return (await session.get(Topic, task.room_id)).id

    assert line.task_id != client.portal.call(room_of_the_line)
    assert all(row.conversation_id == line.task_id for row in line.deliveries())
