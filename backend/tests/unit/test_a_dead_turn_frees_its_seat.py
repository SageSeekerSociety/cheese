"""A turn the sweep finds dead stops holding its seat.

The bug this covers: a session that dies inside its work never reports a
result or a terminal for the inputs it was holding. They stayed unfinished, so
the seat refused every later message: each delivery was admitted, handed back
undispatched, and retried every 40 seconds for as long as anyone looked, with a
"concurrent turns are full" notice posted into the room every time.
"""

import importlib.util
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import select, update

from app.domain.agent.models import AgentTurn
from app.domain.agent.runtime import AgentWorkRunner, InProcessBroker
from app.domain.block.models import AuthorType, Block, BlockKind
from app.domain.delivery.answer_ownership import seat_has_unfinished_input
from app.domain.delivery.input_identity import InputEffects, InputIdentity, InputReceipt
from app.domain.delivery.models import NativeInput
from app.domain.delivery.receipts import held_blocks, record_receipt, register_input
from app.domain.topic.models import Topic
from tests.turn_log import a_topic, open_turn, turn_row
from tests.unit.test_runtime import _SweepChat

SEAT = "cheese-test"
SESSION = "sess-dead"


async def _inputs_into(factory, topic_id, turn_id):
    """What a session working on ``turn_id`` holds: the prompt and a message
    steered in, both read, and a second steer it never got to read."""
    async with factory() as session:
        project_id = (await session.get(Topic, topic_id)).project_id
        answer = Block(
            id=uuid.uuid4(),
            project_id=project_id,
            conversation_id=topic_id,
            kind=BlockKind.message,
            author_type=AuthorType.participant,
            author="u",
            content="answer",
            meta={"consumed_turn": None},
        )
        session.add(answer)
        await session.commit()

    def identity(input_id):
        return InputIdentity(
            project_id, topic_id, SEAT, "claude_code", SESSION, input_id, turn_id
        )

    prompt = identity(turn_id)
    steered = identity(uuid.uuid4())
    unread = identity(uuid.uuid4())
    async with factory() as session:
        await register_input(session, prompt, InputEffects(held_block_ids=(answer.id,)))
        await register_input(session, steered, InputEffects())
        await register_input(session, unread, InputEffects())
        await session.commit()
    for taken in (prompt, steered):
        async with factory() as session:
            await record_receipt(session, InputReceipt(taken, "native_echo", turn_id))
            await session.commit()
    return project_id, answer.id


async def _rows(factory, topic_id):
    async with factory() as session:
        return list(
            await session.scalars(
                select(NativeInput).where(NativeInput.conversation_id == topic_id)
            )
        )


@pytest.mark.anyio
async def test_the_sweep_ends_a_dead_turns_inputs_and_frees_its_seat(db_factory):
    topic = await a_topic(db_factory)
    turn = await open_turn(
        db_factory,
        topic,
        delivered=True,
        age_s=3600,
        resendable=False,
        session_id=SESSION,
    )
    project, answer = await _inputs_into(db_factory, topic, turn)
    async with db_factory() as session:
        assert await seat_has_unfinished_input(session, topic, SEAT)

    chat = _SweepChat(db_factory, live_screen=False, dead_sessions={SESSION})
    await AgentWorkRunner(InProcessBroker()).sweep_orphans(chat)

    row = await turn_row(db_factory, turn)
    assert row is not None and row.stopped_at is not None
    async with db_factory() as session:
        assert not await seat_has_unfinished_input(session, topic, SEAT)
        # Whether the answer was taken is still unknown: it stays held, so it
        # is never sent a second time.
        assert await held_blocks(
            session, project_id=project, topic_id=topic, recipient_handle=SEAT
        ) == {answer}
    rows = await _rows(db_factory, topic)
    assert len(rows) == 3
    assert all(row.completed_at is None for row in rows)
    assert all(row.termination == "orphaned" for row in rows)


@pytest.mark.anyio
async def test_a_turn_the_sweep_leaves_alone_keeps_its_seat(db_factory):
    topic = await a_topic(db_factory)
    turn = await open_turn(
        db_factory,
        topic,
        delivered=True,
        age_s=3600,
        resendable=False,
        session_id=SESSION,
    )
    await _inputs_into(db_factory, topic, turn)

    # Its session still answers: the turn is being worked, not dead.
    chat = _SweepChat(db_factory, live_screen=True, seat_state="live")
    await AgentWorkRunner(InProcessBroker()).sweep_orphans(chat)

    row = await turn_row(db_factory, turn)
    assert row is not None and row.stopped_at is None
    async with db_factory() as session:
        assert await seat_has_unfinished_input(session, topic, SEAT)
    assert all(row.terminated_at is None for row in await _rows(db_factory, topic))


_HEAL = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "7d2e9a41c6b3_end_inputs_of_turns_that_already_ended.py"
)


async def _heal(factory) -> None:
    """The migration's own ``upgrade()``, on the path ``alembic upgrade`` takes."""
    spec = importlib.util.spec_from_file_location("_heal", _HEAL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def apply(conn) -> None:
        with Operations.context(MigrationContext.configure(conn)):
            module.upgrade()

    async with factory() as session:
        await (await session.connection()).run_sync(apply)
        await session.commit()


async def _ended(factory, turn_id, ago: timedelta | None) -> None:
    async with factory() as session:
        await session.execute(
            update(AgentTurn)
            .where(AgentTurn.id == turn_id)
            .values(stopped_at=None if ago is None else datetime.now(UTC) - ago)
        )
        await session.commit()


@pytest.mark.anyio
async def test_seats_held_by_turns_that_ended_before_the_fix_are_freed(db_factory):
    long_ago = await a_topic(db_factory, title="ended long ago")
    just_now = await a_topic(db_factory, title="just ended")
    running = await a_topic(db_factory, title="still running")
    turns = {}
    for topic in (long_ago, just_now, running):
        turns[topic] = await open_turn(db_factory, topic, delivered=True, age_s=7200)
        await _inputs_into(db_factory, topic, turns[topic])
    await _ended(db_factory, turns[long_ago], timedelta(hours=2))
    await _ended(db_factory, turns[just_now], timedelta(minutes=10))

    await _heal(db_factory)

    async with db_factory() as session:
        assert not await seat_has_unfinished_input(session, long_ago, SEAT)
        # A turn that only just ended may still have its result on the way, and
        # one still running is owed everything.
        assert await seat_has_unfinished_input(session, just_now, SEAT)
        assert await seat_has_unfinished_input(session, running, SEAT)
    healed = await _rows(db_factory, long_ago)
    assert all(row.termination == "orphaned" for row in healed)
    assert all(row.completed_at is None for row in healed)
    assert all(row.held_block_ids for row in healed if row.input_id == turns[long_ago])
    for topic in (just_now, running):
        assert all(row.terminated_at is None for row in await _rows(db_factory, topic))
