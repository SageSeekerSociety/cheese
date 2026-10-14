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

from app.core.config import settings
from app.domain.agent_instance.models import AgentInstance
from app.domain.block.models import AuthorType, Block, BlockKind
from app.domain.delivery.models import Delivery, NativeInput
from app.domain.delivery.timer import deliver_due, give_up_stale
from app.domain.notice_meta import EVENT_WAKE_MISSED
from app.domain.notification.models import NotificationType
from app.domain.room_task.models import Task, TaskStatus
from app.domain.run_record.models import RunRecord
from app.domain.topic.models import Topic, TopicStatus
from app.domain.wake_watch import beats, catchup
from app.domain.wake_watch.beats import Beat, Ticking, stall_record_id
from app.domain.wake_watch.observe import watch
from tests.integration.test_same_handle_note_and_timed_delivery import (
    _project,
    _room,
    _seat,
)

MARKER = "【20 分钟巡检·定时器】"
#: #1343 那条线自己的对话 —— 总调度的定时器把每一拍发到这里。写死在这儿是有意的：
#: 期望值不从 `beats` 取，常量被改成别的对话时这一条才红得起来。
DISPATCH_LINE = uuid.UUID("998a4d26-a7d7-4313-b3f5-b22304182d5d")
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

    def deliveries(self, on: uuid.UUID | None = None) -> list[Delivery]:
        """这条线上记下的投递。

        默认只捞落在这条线自己那段对话上的（也就是该落的地方）。要看它有没有落错
        地方，就传别处（比如它所在的房间）去捞 —— 传了才知道落对没有，不传的话
        「都落在 `task_id` 上」是这句 where 自己保证的，证不了什么。
        """
        where = self.task_id if on is None else on

        async def read() -> list[Delivery]:
            async with self.client.test_factory() as session:
                return list(
                    await session.scalars(
                        select(Delivery).where(Delivery.conversation_id == where)
                    )
                )

        return self.client.portal.call(read)

    @property
    def room_id(self) -> uuid.UUID:
        return uuid.UUID(self.room)

    def archive(self) -> None:
        """把这条线所在的房间归档掉。"""

        async def write() -> None:
            async with self.client.test_factory() as session:
                topic = await session.get(Topic, self.room_id)
                topic.status = TopicStatus.archived
                await session.commit()

        self.client.portal.call(write)

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
    """去重键按停摆窗口算：巡检看多少遍，账上只有一条。

    两遍都补同一个座位时，账本上那个唯一索引也拦得住，所以「看两遍只有一条」证明
    不了这一层还查了一次事件 id。这里把那次查询单独试出来：同一场停摆，账上已经
    有一条补给了**别人**的（座位在中途换了就是这个样子），这一遍不该再补一条 ——
    那一刻在场的是谁就叫谁，叫过了就不再叫第二个。
    """
    _stalled(line)
    window = stall_record_id(line.beat, AT)

    async def someone_answered_it() -> None:
        async with client.test_factory() as session:
            session.add(
                Delivery(
                    event_id=window,
                    recipient_handle="cheese-someone-else",
                    conversation_id=line.task_id,
                    dedup_key=f"{window}:cheese-someone-else",
                    type="ROOM_NOTICE",
                    payload={},
                    event_at=AT,
                    recorded_at=AT,
                )
            )
            await session.commit()

    client.portal.call(someone_answered_it)

    line.look(STALLED)

    # 只补过一条，而且是原来那条；这一遍一条都没添。
    (only,) = line.deliveries()
    assert only.recipient_handle == "cheese-someone-else"
    assert "catch_up_seat" not in line.record()


def test_another_stalls_delivery_does_not_answer_this_one(client, line):
    """账上别的停摆窗口那一条，不算这一场停摆补过了。

    线上账本里几乎总有别的还没送出去的投递，所以「这一场补过没有」只能按**这一次停
    摆**的事件 id 去圈：圈松了，别人那一条会被当成「已经补过」，补跳从此静默地再也
    发不出去 —— 它是平台的指令，判失败不在那条线上说话，连「没补成」都没人知道。
    诱饵落在别的对话上，是为了让这里捞到的只可能是这一次补跳那一条。
    """
    _stalled(line)
    other = stall_record_id(line.beat, AT - timedelta(days=1))

    async def an_earlier_stall_is_still_pending() -> None:
        async with client.test_factory() as session:
            session.add(
                Delivery(
                    event_id=other,
                    recipient_handle=line.seat,
                    conversation_id=uuid.uuid4(),
                    dedup_key=f"{other}:{line.seat}",
                    type="ROOM_NOTICE",
                    payload={},
                    event_at=AT - timedelta(days=1),
                    recorded_at=AT - timedelta(days=1),
                )
            )
            await session.commit()

    client.portal.call(an_earlier_stall_is_still_pending)

    assert line.look(STALLED) == {"stalled": 1}

    (delivery,) = line.deliveries()
    assert delivery.event_id == stall_record_id(line.beat, AT)


def test_the_catch_up_carries_the_windows_dedup_key(client, line):
    """补出来的那一条，去重键就是「这次停摆 + 这个座位」。"""
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
    """投递上记的是同一件事：这一拍该发没发，而且它是平台的指令。

    换一套事件码会让「这一拍断过几次」在账本上分成两处数。类型也得是这条：它是平台
    自己发的指令（`ROOM_NOTICE`），不是点了某个人的那种 —— 判失败时说不说话就由它
    决定（`delivery/timer.py` 的 `give_up_stale` 只替人的消息说）。
    """
    _stalled(line)
    line.look(STALLED)

    (delivery,) = line.deliveries()
    assert delivery.payload["eventType"] == EVENT_WAKE_MISSED
    assert delivery.type == NotificationType.ROOM_NOTICE


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

    硬塞进去的那一条会被入场闸挂住，一路重试到放弃，最后只落成账本上一行 failed。
    停摆还在，判的还是同一次，等下一遍不损失什么。
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
    """补跳落在那条线自己的对话里，不是它所在的房间。

    落到房间上是这条路上最像样的错法：叫起来的是同一个座位，但它手里的上下文是
    另一条线。所以两边都捞一遍 —— 捞得到的那个才是它落的地方。
    """
    _stalled(line)

    async def room_of_the_line() -> uuid.UUID:
        async with client.test_factory() as session:
            task = await session.get(Task, line.task_id)
            return (await session.get(Topic, task.room_id)).id

    room = client.portal.call(room_of_the_line)
    assert line.task_id != room, "这一条测不出东西：线和房间是同一个"

    line.look(STALLED)

    assert len(line.deliveries()) == 1
    assert line.deliveries(on=room) == []


def test_the_delivery_says_which_room_it_is_about(client, line):
    """投递上的 `topicId` 是房间，不是这条线自己的对话。

    别的生产者都是这么填的（`topic_landing`、`api/task_instructions`），读它的人
    也照房间读（推送拼的是房间链接）。这里填成任务 id，就是一个没人查得出来的、
    和别处不一样的 id。
    """
    _stalled(line)

    line.look(STALLED)

    (delivery,) = line.deliveries()
    assert delivery.payload["topicId"] == str(line.room_id)


def test_the_shipped_line_is_the_timer_beat_of_the_dispatcher_line():
    """线上的那条线就是定时器那一拍所在的对话。

    测试里那条线是造出来的，答不出「真的会不会补对人」，所以期望值写死在这里、不从被
    检查的那一处取：常量被换成别的对话、或者认这一拍的标记被改掉，这条就红。（写死的
    是 #1343 那条线的 id 和这一拍留在正文里的标记；总调度那条线真换了地方时，改的是
    这里和 `beats`，两边一起。）
    """
    assert catchup.LINE == DISPATCH_LINE
    assert beats.TIMER.conversation_id == DISPATCH_LINE
    assert beats.TIMER in beats.WATCHED
    assert beats.HEARTBEAT in beats.WATCHED
    assert beats.TIMER.marker == MARKER and not beats.TIMER.stamp_in_content
    assert beats.HEARTBEAT.marker_first and beats.HEARTBEAT.stamp_in_content


def test_an_archived_line_is_never_handed_a_catch_up(client, line):
    """归档了的那条线不点名 —— 不是先记一条、再让投递那一侧判它失败。

    投递派发见到「房间归档」会把这一行判成 failed，而这一跳是平台的指令
    （`ROOM_NOTICE`），判失败不往线上说话（`delivery/timer.py` 的 `give_up_stale`）。
    更麻烦的是这一次停摆的去重键跟着账上那一行走，而那一拍不重新走起来就一直是同一个
    ——留下的那行 failed 会让后面每一遍都认成「补过了」，这一跳再也不会来了。
    """
    _stalled(line)
    line.archive()

    assert line.look(STALLED) == {"stalled": 1}
    assert line.deliveries() == []
    # 判据照记：这一拍是真停了，只是这一遍点不了名。
    assert line.record()["stalled_minutes"] == 61
    assert "catch_up_seat" not in line.record()


def test_a_seat_with_no_one_behind_it_is_not_called(client, line):
    """那一侧没有活着的座位：不点名，也不留一条注定失败的投递。

    这一条支路值得单独测：`AgentInstanceService` 解析座位时**不看** `is_active`
    （`agent_instance/repositories.py` 的 `list_for_project` 不过滤），所以被停用的
    座位照样解析得出来 —— 那位已经不在项目里了，记下的那一条会被派发判失败，而失败的
    那一行占住这一次停摆的去重键（理由同上面归档那条）。
    """
    _stalled(line)

    async def deactivate() -> None:
        async with client.test_factory() as session:
            instances = await session.scalars(
                select(AgentInstance).where(
                    AgentInstance.project_id == uuid.UUID(line.project)
                )
            )
            for instance in instances:
                instance.is_active = False
            await session.commit()

    client.portal.call(deactivate)

    assert line.look(STALLED) == {"stalled": 1}
    assert line.deliveries() == []
    assert "catch_up_seat" not in line.record()


def test_a_line_that_is_not_there_is_not_called(client, line, monkeypatch):
    """`#1343` 那条线整个找不到了：不点名，也不炸。

    找得到的那一条（任务行、房间、项目）是这条路上最前面的一次查找，四样里少一样都
    没地方落脚。这里把线指到一个不存在的对话上：少的是任务那一行，和真删掉它的效果
    一样，但不用去动库里的行。直接调 `summon`（不经过 `watch` 那一层 `_call_summon`
    的兜底）才测得到这一条：不加守卫那一下是抛属性错误，而守卫抛出来的错会被兜底吞
    掉、外面看起来一模一样。
    """
    _stalled(line)
    monkeypatch.setattr(catchup, "LINE", uuid.uuid4())

    assert (
        client.portal.call(
            functools.partial(
                catchup.summon,
                client.test_request_factory,
                line.beat,
                Ticking(at=AT, block_at=AT),
                timedelta(minutes=61),
                STALLED,
            )
        )
        is None
    )
    assert line.deliveries() == []
    # 判据照记：这一拍是真停了，找不到落脚的地方是另一回事。
    assert line.look(STALLED) == {"stalled": 1}
    assert line.record()["stalled_minutes"] == 61


def test_a_catch_up_that_never_reached_the_seat_is_handed_again(client, line):
    """半路死掉的那一条不算补过：下一遍把它重新排上，而不是永远补不上。

    派发判失败的每一条都在发送之前，投递那一侧放弃它（`delivery/timer.py` 的
    `give_up_stale`：「半个小时没起来」）也在发送之前 —— 判成失败就是没送到。可这次
    停摆的去重键由「哪一拍、停在哪一刻」算出，那一拍不重新走起来就一直是同一个：留下
    那行 `failed` 就等于把这一次停摆的地址永久占住，谁也不再补，而它是平台的指令，判
    失败不往那条线上说话 —— 连「没补成」都没人知道。
    """
    _stalled(line)
    line.look(STALLED)

    # 走一遍真实的那条死路：派发认领过它（`attempts` 是判超时的门槛），半个小时没
    # 起来，投递那一侧把它放弃掉。
    async def never_started() -> None:
        async with client.test_factory() as session:
            row = (await session.scalars(select(Delivery))).one()
            row.recorded_at = row.recorded_at - timedelta(hours=1)
            row.attempts = 1
            await session.commit()
        await give_up_stale(client.test_request_factory, chat=object())

    client.portal.call(never_started)
    (dead,) = line.deliveries()
    assert dead.state == "failed"

    # 停摆还在：下一遍又补一次 —— 排的是同一行，不是又记一条。运行记录那一条早就
    # 在（判据那一半记过了），所以这一遍不新记，只有补跳这一半有动作。
    assert line.look(STALLED + timedelta(hours=2)) == {}
    (again,) = line.deliveries()
    assert again.id == dead.id
    assert again.state == "pending"
    assert again.attempts == 0
    assert again.recipient_handle == line.seat
    assert again.dedup_key == f"{stall_record_id(line.beat, AT)}:{line.seat}"
    # 话跟着这一遍的时刻重写：那一句里的分钟数是判的时候算出来的。
    assert "181" in again.payload["content"].splitlines()[0]


def test_a_catch_up_that_a_different_seat_gave_up_on_is_recorded_anew(client, line):
    """死信是补给**别人**的：这一遍照样点现在坐着的那位。

    座位中途换了人就是这个样子。那一行不能捞回来接着用 —— 收件人写死在行上，捞回来
    等于把这一跳又发给已经不在那条线上的那位；也不该被它挡住。
    """
    _stalled(line)
    window = stall_record_id(line.beat, AT)

    async def gave_up() -> None:
        async with client.test_factory() as session:
            session.add(
                Delivery(
                    event_id=window,
                    recipient_handle="cheese-who-left",
                    conversation_id=line.task_id,
                    dedup_key=f"{window}:cheese-who-left",
                    type="ROOM_NOTICE",
                    payload={"content": "【补跳】平台侧观察者：…"},
                    event_at=AT,
                    recorded_at=AT,
                    state="failed",
                    attempts=1,
                )
            )
            await session.commit()

    client.portal.call(gave_up)

    assert line.look(STALLED) == {"stalled": 1}
    rows = line.deliveries()
    assert len(rows) == 2, "死信那一行不在，或者又记重了"
    assert {row.recipient_handle for row in rows} == {"cheese-who-left", line.seat}


def test_the_shipped_default_keeps_the_catch_up_on():
    """出厂就是开着的：影子期只判不补，这一半是判完之后真的补。

    默认值是个产品决定（2026-10-13 总调度定的落地范围），不是随手可翻的调试开关。
    接线那条测的是「读了这个开关」，默认值本身谁也测不到 —— 翻成关，补跳就悄悄不
    发了，而所有别的测试都是显式设过开关的。
    """
    assert settings.wake_watch_catchup is True


@pytest.mark.parametrize("enabled", [True, False])
def test_the_wiring_reads_the_flag(monkeypatch, enabled):
    """开关真被接线读了：开着接的是补跳那一句，关着传 None。

    单独的 `summon` 测得到补跳本身，测不到「它有没有被接上去」—— 接线那一行写
    成 `summon = None`，只测 `summon` 的那一组一条都不会红。接上去的也必须是补跳
    本身：接成别的什么都不做的一句，一样一条都不会红。
    """
    from app.core.background import PeriodicJobs
    from app.domain.wake_watch import observe as observe_module
    from app.domain.wake_watch import periodic as periodic_module

    monkeypatch.setattr(settings, "wake_watch_catchup", enabled)
    seen: dict = {}

    async def fake_watch(sessions, *, now=None, watched=None, summon=None):
        seen["summon"] = summon
        return {}

    monkeypatch.setattr(observe_module, "watch", fake_watch)
    jobs = PeriodicJobs()
    periodic_module.register_periodic_jobs(jobs, sessions=lambda: None)

    client_loop = __import__("asyncio").new_event_loop()
    try:
        client_loop.run_until_complete(jobs.ordered(["wake watch"])[0]._job())
    finally:
        client_loop.close()

    assert (seen["summon"] is not None) is enabled
    if enabled:
        # 接的必须是补跳那一句，不是别的什么都行的一句。
        assert seen["summon"].func is catchup.summon


def test_a_judged_stall_is_still_recorded_when_the_catch_up_raises(client, line):
    """补跳那一步抛错，这一遍的判据照记，后面几拍也照样判。

    补跳碰库、解析座位，比判据多好几处会出错的地方。它出错时把整遍带走的话，观察
    者自己就哑了 —— 而它哑掉这件事，没有任何别的东西在看。
    """
    _stalled(line)

    async def boom(*args, **kwargs):
        raise RuntimeError("补跳这一半坏了")

    assert client.portal.call(
        functools.partial(
            watch,
            client.test_request_factory,
            now=STALLED,
            watched=(line.beat,),
            summon=boom,
        )
    ) == {"stalled": 1}
    assert line.record()["stalled_minutes"] == 61
    assert "catch_up_seat" not in line.record()
    assert line.deliveries() == []


def test_the_catch_up_records_the_moment_the_stall_became_true(client, line):
    """投递上的时刻是「判出停摆的那一刻」，不是这一遍看它的时刻。

    这一笔是补一笔早就该发生的事：按巡检这一遍的时刻记，事后对时间线时，它会比真实
    那一次晚一整个巡检周期（甚至更多）。写死 60 分钟，不写 `beat.stall_after`：拿
    那个属性来比，比的是「有没有跟着这一拍走」，比不出 60 这个数本身。
    """
    _stalled(line)

    line.look(STALLED + timedelta(hours=3))

    (delivery,) = line.deliveries()
    assert delivery.event_at == AT + timedelta(minutes=60)
