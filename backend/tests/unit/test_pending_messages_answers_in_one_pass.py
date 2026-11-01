"""恢复扫描按会话批量问「答在哪、席位还拿着没有」，不逐条问。

扫描要对每条待答消息问两件事：它答在哪个会话（`thread.services.answered_in`，
每条两条到四条 SQL），以及那个会话的席位还有没有没读完的输入
（`delivery.input_holds.seat_has_unfinished_input`，每条一条）。一批积压来自少数
几个会话，逐条问就是 N 倍往返；批量版各发一次 IN 查询再分发，判据逐条照旧。

这里钉三件事：

- 50 条积压、5 个会话时扫描的总查询数不超过 15，且和 10 条积压时一样多——积压
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
from app.domain.block.models import BlockKind
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
from app.domain.thread.services import answered_in, answered_in_many
from app.domain.topic.models import Topic, TopicKind, TopicStatus
from app.domain.topic.services import TopicService
from tests.integration.conftest import project_service, registered

BUDGET = 15
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
    """One channel, five waiting messages that named the seat, each with its 支线.

    A main-line message is answered in the 支线 opened under it; the messages
    that follow it were said inside that 支线. Returns (project id, answer
    places, the block ids waiting in each, in the order the scan sees them).
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
                meta={"agent_recipient": {"handle": HANDLE, "mentioned": True}},
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
            waiting[thread.id] = [root.id]
            for other in range(per_conversation - 1):
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

    async def answered_one_by_one(session, blocks):
        return {block.id: await answered_in(session, block) for block in blocks}

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
        per_message.setattr(pending_messages, "answered_in_many", answered_one_by_one)
        per_message.setattr(
            pending_messages, "seats_with_unfinished_input", holds_one_by_one
        )
        count, started = await _scan(factory)
    return batched_count, batched, count, started


@pytest.mark.anyio
async def test_the_scan_asks_each_conversation_once_however_many_wait(
    db_factory, no_seat_reads
):
    """50 waiting messages in 5 conversations cost what 10 do, and at most 15.

    Measured: 9 statements at 10 waiting messages, 9 at 50 and 9 at 100,
    against 35, 115 and 215 with the per-message reads put back. The fixed part
    is the scan's own batch reads (the queued page, begun turns, open turns, the
    answers and the holds); the count is higher in a channel whose waiting
    message still has no 支线, where the batch also writes one.
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
async def test_batch_reads_answer_like_their_single_reads(db_factory):
    """`answered_in_many` and `seats_with_unfinished_input` are the same answer.

    Asked of rows whose answers differ: a message inside a 支线, a channel
    main-line message with one already, one with none yet (the batch has to open
    it), an archived channel (no 支线 opens there), and a held seat next to a
    finished input.
    """
    async with db_factory() as session:
        await registered(session, "u")
        project = await project_service(session).create(
            name="P", owner_handle="u", forge_kind="github_app"
        )
        room = await TopicService(session).create(
            project_id=project.id, title="T", created_by="u"
        )
        archived_room = await TopicService(session).create(
            project_id=project.id, title="归档的", created_by="u"
        )
        archived_room.status = TopicStatus.archived
        blocks = BlockRepository(session)

        def queued(conversation_id: uuid.UUID, content: str):
            return blocks.add(
                project_id=project.id,
                conversation_id=conversation_id,
                author="u",
                author_type=AuthorType.participant,
                content=content,
                meta={"agent_recipient": {"handle": HANDLE, "mentioned": True}},
            )

        root = await queued(room.id, "有支线的主线")
        thread = Thread(
            id=uuid.uuid4(),
            project_id=project.id,
            room_id=room.id,
            root_block_id=root.id,
            created_by="u",
        )
        session.add(thread)
        await session.flush()
        in_thread = await queued(thread.id, "支线里")
        in_archived = await queued(archived_room.id, "归档频道的主线")
        # Two identical main-line messages with no 支线: one is answered one at a
        # time, the other through the batch. Each has to open its own, and
        # neither may answer in the other's (or in the room).
        solo = await queued(room.id, "单独答的")
        through_batch = await queued(room.id, "批量答的")
        also_through_batch = await queued(room.id, "批量答的第二条")
        await session.commit()

        # The single read runs first, on rows whose answer does not depend on
        # what the other side opened.
        single = {
            block.id: await answered_in(session, block)
            for block in (in_thread, in_archived, root)
        }
        batch = await answered_in_many(session, [in_thread, in_archived, root])
        assert batch == single
        assert batch[in_thread.id] == thread.id
        assert batch[root.id] == thread.id
        assert batch[in_archived.id] == archived_room.id

        solo_place = await answered_in(session, solo)
        # Two at once, so the batch also writes several 支线 in one statement.
        places = await answered_in_many(session, [through_batch, also_through_batch])
        batch_place = places[through_batch.id]
        other_place = places[also_through_batch.id]
        assert room.id not in (solo_place, batch_place, other_place)
        assert len({solo_place, batch_place, other_place}) == 3
        for place, root_block in (
            (solo_place, solo),
            (batch_place, through_batch),
            (other_place, also_through_batch),
        ):
            opened = await session.get(Thread, place)
            assert opened is not None and opened.root_block_id == root_block.id

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
        assert (
            await seat_has_unfinished_input(session, archived_room.id, HANDLE) is False
        )


@pytest.mark.anyio
async def test_a_task_and_a_dm_are_answered_where_they_were_said(db_factory):
    """The batch answers in place for the conversations that are not channels.

    A task's message and a private chat's are answered where they were said —
    neither a task nor a DM has a 支线 — and an attachment in a channel's main
    line is not a message, so nothing opens under it either. The batch has to
    read those three apart from a channel's own message, which is the row it
    does act on.
    """
    async with db_factory() as session:
        await registered(session, "u")
        project = await project_service(session).create(
            name="P", owner_handle="u", forge_kind="github_app"
        )
        room = await TopicService(session).create(
            project_id=project.id, title="T", created_by="u"
        )
        # A task's row registers its conversation (the `tasks` insert trigger),
        # which is all `answered_in` reads it for.
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
        in_task = await blocks.add(
            project_id=project.id,
            conversation_id=task_id,
            author="u",
            author_type=AuthorType.participant,
            content="任务里的",
            meta={"agent_recipient": {"handle": HANDLE, "mentioned": True}},
        )
        in_dm = await blocks.add(
            project_id=project.id,
            conversation_id=dm.id,
            author="u",
            author_type=AuthorType.participant,
            content="私聊里的",
            meta={"agent_recipient": {"handle": HANDLE, "mentioned": True}},
        )
        attachment = await blocks.add(
            project_id=project.id,
            conversation_id=room.id,
            author="u",
            author_type=AuthorType.participant,
            content="一张图",
            kind=BlockKind.attachment,
            meta={"agent_recipient": {"handle": HANDLE, "mentioned": True}},
        )
        main_line = await blocks.add(
            project_id=project.id,
            conversation_id=room.id,
            author="u",
            author_type=AuthorType.participant,
            content="频道主线的消息",
            meta={"agent_recipient": {"handle": HANDLE, "mentioned": True}},
        )
        await session.commit()

        rows = [in_task, in_dm, attachment, main_line]
        # The single read goes first: it is the one that opens the 支线 under
        # `main_line`, so the batch that follows is compared against a state it
        # did not create.
        single = {block.id: await answered_in(session, block) for block in rows}
        batch = await answered_in_many(session, rows)
        assert batch == single
        assert batch[in_task.id] == task_id
        assert batch[in_dm.id] == dm.id
        assert batch[attachment.id] == room.id
        assert batch[main_line.id] not in (room.id, attachment.id)
        assert await session.get(Thread, batch[main_line.id]) is not None
