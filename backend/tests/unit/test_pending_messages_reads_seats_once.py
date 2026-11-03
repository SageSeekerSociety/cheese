"""恢复扫描一次问齐一批消息的坐席，不逐条问。

扫描启动一轮之前要问「这条消息所在的对话里，坐席是谁、它按哪个座位署名」
（`agent.queries.seats_in_conversations`）。一批积压来自少数几段对话，逐条问就是
N 倍往返；批量版每段对话的地方只读一次，答复按 (conversation, handle) 分发。

这里钉三件事：

- 5 条待答消息、2 段对话时的扫描语句数，与 25 条时相同；
- 批量版对每个 (conversation, handle) 的答复与逐条 `conversation_seat` 逐字段相同；
- 把批量查表换回逐条调用，同一条计数断言就红（随消息数增长）。

消息故意在坐席解析之后被跳过（`instance_id` 指向另一台实例）：解析成功的消息本来
就会被 `seats[key]` 去重，只有被跳过的那些在逐条版里会一条一条重问，这正是要量的
N+1。
"""

import uuid

import pytest
from sqlalchemy import event, select

from app.domain.agent import pending_messages, queries
from app.domain.agent_instance.models import AgentInstance
from app.domain.block.repositories import BlockRepository
from app.domain.conversation.numbering import lock_conversations
from app.domain.event_author import AuthorType
from app.domain.identity.handles import agent_instance_handle
from app.domain.room_task.models import Task as TaskRow
from app.domain.thread.models import Thread
from app.domain.topic.services import TopicService
from tests.integration.conftest import project_service, registered

BUDGET = 80


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


async def _seed(factory):
    """One channel, 5 waiting messages in 2 conversations: a 支线 and a task.

    Returns (project id, room id, 支线 id, task id, the agent's seat, the
    agent's handle). Every message names the project's agent but another
    instance, so its seat is resolved and it is then skipped — the path where a
    per-message scan asks again for every message.
    """
    async with factory() as session:
        await registered(session, "u")
        project = await project_service(session).create(
            name="P", owner_handle="u", forge_kind="github_app"
        )
        room = await TopicService(session).create(
            project_id=project.id, title="T", created_by="u"
        )
        instance = await session.scalar(
            select(AgentInstance).where(AgentInstance.project_id == project.id)
        )
        assert instance is not None
        seat = agent_instance_handle(instance.id)
        blocks = BlockRepository(session)
        root = await blocks.add(
            project_id=project.id,
            conversation_id=room.id,
            author="u",
            author_type=AuthorType.participant,
            content="支线的根",
        )
        thread = Thread(
            id=uuid.uuid4(),
            project_id=project.id,
            room_id=room.id,
            root_block_id=root.id,
            created_by="u",
        )
        session.add(thread)
        task_id = uuid.uuid4()
        session.add(
            TaskRow(id=task_id, project_id=project.id, room_id=room.id, title="任务")
        )
        await session.flush()
        for index in range(3):
            await _said(blocks, project.id, thread.id, instance.handle, index, "支线")
        for index in range(2):
            await _said(blocks, project.id, task_id, instance.handle, index, "任务")
        await session.commit()
        return project.id, room.id, thread.id, task_id, seat, instance.handle


async def _said(blocks, project_id, conversation_id, handle, index, where):
    """One waiting message that names ``handle`` but another instance."""
    return await blocks.add(
        project_id=project_id,
        conversation_id=conversation_id,
        author="u",
        author_type=AuthorType.participant,
        content=f"{where}第 {index} 条",
        meta={
            "agent_recipient": {
                "handle": handle,
                "instance_id": str(uuid.uuid4()),
                "mentioned": True,
            }
        },
    )


async def _more(factory, project_id, thread_id, task_id, handle, *, per_conversation):
    async with factory() as session:
        # Both conversations written into, named first (`conversation.numbering`).
        await lock_conversations(session, [thread_id, task_id])
        blocks = BlockRepository(session)
        for index in range(per_conversation):
            await _said(blocks, project_id, thread_id, handle, index, "后补支线")
            await _said(blocks, project_id, task_id, handle, index, "后补任务")
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


@pytest.mark.anyio
async def test_the_scan_reads_the_seats_once_however_many_wait(db_factory):
    """25 waiting messages cost what 5 do, in the same two conversations."""
    project_id, _, thread_id, task_id, _, handle = await _seed(db_factory)

    five, started = await _scan(db_factory)
    assert five <= BUDGET, f"{five} statements for 5 waiting messages"
    assert started == [], "every message is skipped after its seat is resolved"

    await _more(db_factory, project_id, thread_id, task_id, handle, per_conversation=10)
    twenty_five, started_later = await _scan(db_factory)
    assert twenty_five == five, "backlog size changed the scan's reads"
    assert started_later == []


@pytest.mark.anyio
async def test_the_batch_answers_like_the_single_read(db_factory):
    """The batch is the single read's judgement, pair by pair."""
    _, room_id, thread_id, task_id, seat, handle = await _seed(db_factory)
    missing = uuid.uuid4()
    pairs = [
        (room_id, handle),
        (thread_id, handle),
        (task_id, handle),
        (thread_id, "someone-else"),
        (missing, handle),
        (thread_id, handle),
    ]

    async with db_factory() as session:
        batch = await queries.seats_in_conversations(session, pairs)
        single = {
            pair: await queries.conversation_seat(session, *pair)
            for pair in dict.fromkeys(pairs)
        }

    assert batch == single
    seated = batch[(thread_id, handle)]
    assert seated is not None
    agent, acting = seated
    assert agent.handle == handle
    assert acting == seat
    assert batch[(room_id, handle)] == (agent, seat)
    assert batch[(missing, handle)] is None
    assert batch[(thread_id, "someone-else")] == (agent, seat)


@pytest.mark.anyio
async def test_putting_the_per_message_reads_back_breaks_the_same_budget(
    db_factory, monkeypatch
):
    """The budget is the batch's: one call per message goes red as it grows."""
    project_id, _, thread_id, task_id, _, handle = await _seed(db_factory)
    budgeted, _ = await _scan(db_factory)

    original = queries.seats_in_conversations

    async def one_at_a_time(session, pairs):
        answers = {}
        for pair in pairs:
            answers[pair] = (await original(session, [pair]))[pair]
        return answers

    monkeypatch.setattr(queries, "seats_in_conversations", one_at_a_time)
    per_message_five, _ = await _scan(db_factory)
    assert per_message_five > budgeted

    await _more(db_factory, project_id, thread_id, task_id, handle, per_conversation=10)
    per_message_twenty_five, _ = await _scan(db_factory)
    assert per_message_twenty_five > per_message_five, "N+1: five times the backlog"
