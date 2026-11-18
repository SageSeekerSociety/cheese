"""恢复扫描按会话批量问「席位还拿着没有」，不逐条问；消息答在它被说的地方。

扫描要对每条待答消息问它那个会话的席位还有没有没读完的输入
（`delivery.input_holds.seat_has_unfinished_input`，每条一条）。一批积压来自少数
几个会话，逐条问就是 N 倍往返；批量版发一次 IN 查询再分发，判据逐条照旧。

这里钉三件事：

- 50 条积压、5 个会话时扫描的总查询数不超过 8，且和 10 条积压时一样多——积压
  变多不该多问；
- 批量版对每条消息的答复与逐条版逐字段相同，启动的轮次也一样；
- 把扫描换回逐条版，同一条计数断言就红。这条故意违规是本文件存在的理由：没有
  它，计数断言写成多大都照样绿。

席位解析（`agent.queries.seats_in_conversations`）由另一条改动批量化；这里的计数
只说这一条改动的读，所以给它一个不碰库的替身。聊天服务的 `has_running_turn` 同理。
"""

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import event

from app.domain.agent import pending_messages
from app.domain.block.repositories import BlockRepository
from app.domain.conversation.numbering import lock_conversations
from app.domain.delivery.input_holds import (
    seat_has_unfinished_input,
    seats_with_unfinished_input,
)
from app.domain.delivery.models import NativeInput
from app.domain.event_author import AuthorType
from app.domain.room_task.models import Task as TaskRow
from app.domain.thread.models import Thread
from app.domain.topic.models import Topic, TopicKind, TopicStatus
from app.domain.topic.services import TopicService
from tests.integration.conftest import project_service, registered

BUDGET = 8
HANDLE = "ada"
OTHER = "bob"
INSTANCE = uuid.uuid4()


def _statements(engine):
    """Record every statement the scan sends; returns (list, stop)."""
    statements: list[str] = []

    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", record)
    return statements, lambda: event.remove(
        engine.sync_engine, "before_cursor_execute", record
    )


class _Chat:
    """The scan's in-process half: no turn is running, and it reads no database."""

    def __init__(self, factory):
        self.session_factory = factory

    async def has_running_turn(self, topic_id, agent_handle=None):
        return False


class _Runner:
    """Says the scan may run, and records the turns it starts."""

    ORPHAN_STALE_S = 3600
    owns_sessions = True
    accepting_turns = True

    def __init__(self):
        self.started: list[dict] = []

    def turn_pending(self, block_id) -> bool:
        return False

    def _receive_message(self, chat, room, block_id, **kwargs):
        self.started.append({"room": room, "block_id": block_id, **kwargs})


async def _seated(session, pairs):
    """`seats_in_conversations`, minus its own reads.

    The seat read is batched in its own change; this test measures the scan's
    own reads, so the answer is a stand-in for every pair."""
    return {
        pair: (SimpleNamespace(handle=pair[1], instance_id=INSTANCE), pair[1])
        for pair in dict.fromkeys(pairs)
    }


@pytest.fixture
def no_seat_reads(monkeypatch):
    monkeypatch.setattr("app.domain.agent.queries.seats_in_conversations", _seated)


async def _seed(factory, *, per_conversation: int = 2):
    """One channel with five 支线, and messages waiting in each that named the
    seat. Returns (project id, the 支线, the block ids waiting in each, in the
    order the scan sees them).
    """
    async with factory() as session:
        await registered(session, "u")
        project = await project_service(session).create(
            name="P", owner_handle="u", forge_kind="github_app"
        )
        room = await TopicService(session).create(
            project_id=project.id, title="T", created_by="u"
        )
        blocks = BlockRepository(session)
        places: list[uuid.UUID] = []
        waiting: dict[uuid.UUID, list[uuid.UUID]] = {}
        for index in range(5):
            root = await blocks.add(
                project_id=project.id,
                conversation_id=room.id,
                author="u",
                author_type=AuthorType.participant,
                content=f"主线第 {index} 条",
            )
            thread = Thread(
                id=uuid.uuid4(),
                project_id=project.id,
                room_id=room.id,
                root_block_id=root.id,
                created_by="u",
            )
            session.add(thread)
            await session.flush()
            places.append(thread.id)
            waiting[thread.id] = []
            for other in range(per_conversation):
                block = await blocks.add(
                    project_id=project.id,
                    conversation_id=thread.id,
                    author="u",
                    author_type=AuthorType.participant,
                    content=f"支线里的第 {other} 条",
                    meta={"agent_recipient": {"handle": HANDLE, "mentioned": True}},
                )
                waiting[thread.id].append(block.id)
        await session.commit()
        return project.id, places, waiting


async def _more(factory, project_id, places, *, per_conversation):
    async with factory() as session:
        # Every conversation written into, named first (`conversation.numbering`).
        await lock_conversations(session, places)
        blocks = BlockRepository(session)
        for place in places:
            for index in range(per_conversation):
                await blocks.add(
                    project_id=project_id,
                    conversation_id=place,
                    author="u",
                    author_type=AuthorType.participant,
                    content=f"后补的第 {index} 条",
                    meta={"agent_recipient": {"handle": HANDLE, "mentioned": True}},
                )
        await session.commit()


async def _add_held_input(factory, project_id, conversation_id):
    """One input written to the seat's session and never read: its fate is
    unknown, and it holds that seat by itself."""
    async with factory() as session:
        now = datetime.now(UTC)
        session.add(
            NativeInput(
                project_id=project_id,
                conversation_id=conversation_id,
                recipient_handle=HANDLE,
                harness="claude",
                native_session_id="native-held",
                input_id=uuid.uuid4(),
                work_id=uuid.uuid4(),
                registered_at=now - timedelta(minutes=5),
            )
        )
        await session.commit()


async def _scan(factory):
    """Run one scan and count what it sent. Returns (count, started turns)."""
    engine = factory.kw["bind"]
    statements, stop = _statements(engine)
    runner = _Runner()
    try:
        await pending_messages.resume_messages(runner, _Chat(factory), source="sweep")
    finally:
        stop()
    return len(statements), runner.started


async def _scan_both_ways(factory):
    """One scan as it is, then one with the per-message reads put back."""
    batched_count, batched = await _scan(factory)

    async def holds_one_by_one(session, pairs):
        holding = set()
        for pair in pairs:
            if await seat_has_unfinished_input(session, *pair):
                holding.add(pair)
        return holding

    # A context of its own: undoing this fixture would also drop the seat
    # stand-in the caller installed, and the second scan would read the seats
    # for real (which is not what is being measured).
    with pytest.MonkeyPatch.context() as per_message:
        per_message.setattr(
            pending_messages, "seats_with_unfinished_input", holds_one_by_one
        )
        count, started = await _scan(factory)
    return batched_count, batched, count, started


@pytest.mark.anyio
async def test_the_scan_asks_each_conversation_once_however_many_wait(
    db_factory, no_seat_reads
):
    """50 waiting messages in 5 conversations cost what 10 do, and at most 8.

    Measured: 6 statements at 10 waiting messages and 6 at 50,
    against 15 at 10 with the per-message hold reads put back. The fixed part
    is the scan's own batch reads (the queued page, begun turns, open turns and
    the holds).
    """
    project_id, places, waiting = await _seed(db_factory)
    await _add_held_input(db_factory, project_id, places[0])

    ten, started_ten = await _scan(db_factory)
    assert ten <= BUDGET, f"{ten} statements for 10 waiting messages"
    # One turn per conversation that can take one; the held seat takes none.
    assert [record["room"] for record in started_ten] == places[1:]
    assert [record["block_id"] for record in started_ten] == [
        waiting[place][0] for place in places[1:]
    ]

    await _more(db_factory, project_id, places, per_conversation=8)
    fifty, started_fifty = await _scan(db_factory)
    assert fifty <= BUDGET, f"{fifty} statements for 50 waiting messages"
    assert fifty == ten, "backlog size changed the scan's own reads"
    assert [record["room"] for record in started_fifty] == places[1:]


@pytest.mark.anyio
async def test_putting_the_per_message_reads_back_breaks_the_same_budget(
    db_factory, no_seat_reads
):
    """The budget above is the batching's, not the fixture's.

    Same rows, same stubs, same assertions; the two runs differ only in whether
    the scan asks per message or per conversation. The per-message run answers
    what the batch answered, field for field, and its cost rises with the
    backlog the batch ignores — which is what lets the first test go red.
    """
    project_id, places, _ = await _seed(db_factory)
    await _add_held_input(db_factory, project_id, places[0])

    budgeted, batched, per_message_ten, single_ten = await _scan_both_ways(db_factory)
    assert budgeted <= BUDGET
    assert per_message_ten > BUDGET
    for field in ("room", "block_id", "recipient_handle", "content"):
        assert [record[field] for record in single_ten] == [
            record[field] for record in batched
        ]

    await _more(db_factory, project_id, places, per_conversation=8)
    still_budgeted, _, per_message_fifty, _ = await _scan_both_ways(db_factory)
    assert still_budgeted == budgeted
    assert per_message_fifty > per_message_ten, "N+1: five times the backlog"


@pytest.mark.anyio
async def test_batch_hold_reads_answer_like_their_single_reads(db_factory):
    """`seats_with_unfinished_input` is `seat_has_unfinished_input` for many
    pairs: a held seat next to a finished input, and a hold under a seat
    nobody asked about."""
    async with db_factory() as session:
        await registered(session, "u")
        project = await project_service(session).create(
            name="P", owner_handle="u", forge_kind="github_app"
        )
        room = await TopicService(session).create(
            project_id=project.id, title="T", created_by="u"
        )
        root = await BlockRepository(session).add(
            project_id=project.id,
            conversation_id=room.id,
            author="u",
            author_type=AuthorType.participant,
            content="有支线的主线",
        )
        thread = Thread(
            id=uuid.uuid4(),
            project_id=project.id,
            room_id=room.id,
            root_block_id=root.id,
            created_by="u",
        )
        session.add(thread)
        await session.flush()
        now = datetime.now(UTC)
        finished = uuid.uuid4()
        session.add_all(
            [
                NativeInput(
                    project_id=project.id,
                    conversation_id=thread.id,
                    recipient_handle=HANDLE,
                    harness="claude",
                    native_session_id="native-held",
                    input_id=uuid.uuid4(),
                    work_id=uuid.uuid4(),
                    registered_at=now,
                ),
                NativeInput(
                    project_id=project.id,
                    conversation_id=room.id,
                    recipient_handle=HANDLE,
                    harness="claude",
                    native_session_id="native-finished",
                    input_id=finished,
                    work_id=finished,
                    registered_at=now,
                    # Read: delivered, and holding nothing.
                    echoed_at=now,
                ),
                # A hold under a seat nobody is asking about in the pair below:
                # the read may only answer the pairs it was given.
                NativeInput(
                    project_id=project.id,
                    conversation_id=thread.id,
                    recipient_handle=OTHER,
                    harness="claude",
                    native_session_id="native-other",
                    input_id=uuid.uuid4(),
                    work_id=uuid.uuid4(),
                    registered_at=now,
                ),
            ]
        )
        await session.commit()
        assert await seats_with_unfinished_input(
            session, [(thread.id, HANDLE), (room.id, OTHER)]
        ) == {(thread.id, HANDLE)}
        assert await seats_with_unfinished_input(session, [(thread.id, OTHER)]) == {
            (thread.id, OTHER)
        }
        # The single read is the same judgement for one pair.
        assert await seat_has_unfinished_input(session, thread.id, HANDLE) is True
        assert await seat_has_unfinished_input(session, room.id, HANDLE) is False
        assert await seat_has_unfinished_input(session, thread.id, OTHER) is True


@pytest.mark.anyio
async def test_a_message_to_the_teammate_is_answered_where_it_was_said(
    db_factory, no_seat_reads
):
    """A message waiting for the AI teammate starts its turn in the conversation
    it was said in: a channel's main line, a task, a private chat. One said in
    a channel's main line is answered there, and no 支线 opens under it."""
    async with db_factory() as session:
        await registered(session, "u")
        project = await project_service(session).create(
            name="P", owner_handle="u", forge_kind="github_app"
        )
        room = await TopicService(session).create(
            project_id=project.id, title="T", created_by="u"
        )
        # A task's row registers its conversation (the `tasks` insert trigger).
        task_id = uuid.uuid4()
        session.add(
            TaskRow(id=task_id, project_id=project.id, room_id=room.id, title="任务")
        )
        await session.flush()
        dm = Topic(
            project_id=project.id,
            title="与芝士私聊",
            kind=TopicKind.topic,
            status=TopicStatus.active,
            is_private=True,
            created_by="u",
        )
        session.add(dm)
        await session.flush()
        blocks = BlockRepository(session)
        said = {}
        for place in (room.id, task_id, dm.id):
            block = await blocks.add(
                project_id=project.id,
                conversation_id=place,
                author="u",
                author_type=AuthorType.participant,
                content="交给芝士的一句",
                meta={"agent_recipient": {"handle": HANDLE, "mentioned": True}},
            )
            said[place] = block.id
        await session.commit()

    _, started = await _scan(db_factory)
    assert {record["room"]: record["block_id"] for record in started} == said
    async with db_factory() as session:
        assert await Thread.of_root(session, said[room.id]) is None
