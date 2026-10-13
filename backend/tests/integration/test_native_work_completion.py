"""Completion transaction evidence; native-shaped result, not a live executor.

Registration/echo are real PostgreSQL operations. Results pass through the
Claude assembler and real ChatService work-close transaction. No provider or
ComputePool is restarted in these tests.
"""

import asyncio
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import event, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.domain.agent.chat import ChatService
from app.domain.agent.gateway_usage import OWN_ROUTE
from app.domain.agent.harness import CLAUDE_CODE
from app.domain.agent.harness.claude_code.events import Assembler
from app.domain.agent.service import AgentResult
from app.domain.agent.turn.state.live import HookWorkState, LiveWork
from app.domain.agent_instance.models import AgentInstance
from app.domain.block.models import Block, consumed_turn
from app.domain.block.repositories import BlockRepository
from app.domain.delivery.input_identity import InputEffects, InputReceipt
from app.domain.delivery.models import NativeInput, TimedDelivery
from app.domain.delivery.receipts import held_blocks, record_receipt, register_input
from app.domain.identity.handles import agent_instance_handle
from app.domain.usage.models import ResourceUsage
from tests.integration import test_own_claude_code_turns as own_turns
from tests.integration.test_native_batch_ownership import _blocks, _identity
from tests.integration.test_same_handle_note_and_timed_delivery import _project, _room


def _state(identity):
    return HookWorkState(
        project_id=identity.project_id,
        topic_id=identity.conversation_id,
        work_id=identity.work_id,
        pending_ids=set(),
        reply_to=None,
        roster=[],
        topic_refs=[],
        continuation_id=None,
        route="gateway",
        acting_agent=identity.recipient_handle,
        agent_pool=None,
        user_text="",
        started_at=datetime.now(UTC),
        agent_instance_handle=identity.recipient_handle,
    )


def _result(identity, **changes):
    record = {
        "type": "result",
        "session_id": identity.native_session_id,
        "is_error": False,
        "result": "",
        "cheese": {"agent_handle": identity.recipient_handle},
    }
    record.update(changes)
    result = Assembler({}, session_id=identity.native_session_id).accept(record)[0]
    assert isinstance(result, AgentResult)
    return result


@pytest.mark.parametrize(
    "case",
    [
        "clean",
        "wrong_work",
        "wrong_session",
        "wrong_receiver",
        "error",
        "interrupted",
        "no_echo",
    ],
)
def test_only_exact_clean_native_work_releases_its_registered_batch(client, case):
    project = uuid.UUID(_project(client, "durable completion"))
    topic = uuid.UUID(_room(client, str(project), "native result"))

    async def run():
        factory = client.test_request_factory
        identity = replace(_identity(project, topic), harness=CLAUDE_CODE)
        ids = await _blocks(factory, project, topic)
        async with factory() as session:
            await register_input(session, identity, InputEffects(held_block_ids=ids))
            if case != "no_echo":
                await record_receipt(
                    session,
                    InputReceipt(
                        identity, "native_echo", execution_work_id=identity.work_id
                    ),
                )
            await session.commit()
        state = _state(identity)
        result = _result(identity)
        if case == "wrong_work":
            state.work_id = uuid.uuid4()
        elif case == "wrong_session":
            result = replace(result, session_id=str(uuid.uuid4()))
        elif case == "wrong_receiver":
            result = replace(result, agent_handle="different-seat")
        elif case == "error":
            result = _result(identity, is_error=True)
        elif case == "interrupted":
            result = _result(
                identity,
                cheese={"agent_handle": identity.recipient_handle, "interrupted": True},
            )
        chat = ChatService.__new__(ChatService)
        chat._sessions, chat._gateway = factory, None
        chat.live = LiveWork()
        await chat.turn_completion.close(state, result)
        await chat.turn_completion.close(state, result)
        async with factory() as session:
            remaining = await held_blocks(
                session,
                project_id=project,
                topic_id=topic,
                recipient_handle=identity.recipient_handle,
            )
            row = await session.scalar(select(NativeInput))
            if case == "clean":
                assert remaining == set()
                assert set(row.released_block_ids) == {str(block) for block in ids}
                blocks = list(
                    await session.scalars(select(Block).where(Block.id.in_(ids)))
                )
                assert {consumed_turn(block) for block in blocks} == {
                    str(identity.work_id)
                }
            else:
                assert remaining == set(ids)
                assert row.released_block_ids == []
        if case == "clean":
            # Later bookkeeping must not resurrect an already released hold.
            async with factory() as session:
                await BlockRepository(session).mark_consumed(list(ids), uuid.uuid4())
                await session.commit()
            await chat.turn_completion.close(state, result)
            async with factory() as session:
                assert (
                    await held_blocks(
                        session,
                        project_id=project,
                        topic_id=topic,
                        recipient_handle=identity.recipient_handle,
                    )
                    == set()
                )

    client.portal.call(run)


def test_completion_commit_abort_rolls_back_release_and_consumption_together(client):
    project = uuid.UUID(_project(client, "completion abort"))
    topic = uuid.UUID(_room(client, str(project), "work"))

    async def run():
        factory = client.test_request_factory
        identity = replace(_identity(project, topic), harness=CLAUDE_CODE)
        ids = await _blocks(factory, project, topic)
        async with factory() as session:
            await register_input(session, identity, InputEffects(held_block_ids=ids))
            await record_receipt(
                session,
                InputReceipt(
                    identity, "native_echo", execution_work_id=identity.work_id
                ),
            )
            await session.commit()
        chat = ChatService.__new__(ChatService)
        chat._sessions, chat._gateway = factory, None
        chat.live = LiveWork()
        state, result = _state(identity), _result(identity)
        aborted = []
        # The listener is on every Session in the process, and the app's
        # periodic jobs commit too: only this task's commits are the close's.
        closing = asyncio.current_task()

        def fail_commit(session):
            if asyncio.current_task() is not closing:
                return
            session.flush()
            aborted.append(True)
            session.execute(text("SELECT 1 / 0"))

        event.listen(Session, "before_commit", fail_commit)
        try:
            with pytest.raises(DBAPIError):
                await chat.turn_completion.close(state, result)
        finally:
            event.remove(Session, "before_commit", fail_commit)
        assert aborted == [True]
        async with factory() as session:
            row = await session.scalar(select(NativeInput))
            assert row.released_block_ids == []
            blocks = list(await session.scalars(select(Block).where(Block.id.in_(ids))))
            assert all(consumed_turn(block) is None for block in blocks)
        await chat.turn_completion.close(state, result)
        async with factory() as session:
            assert (
                await held_blocks(
                    session,
                    project_id=project,
                    topic_id=topic,
                    recipient_handle=identity.recipient_handle,
                )
                == set()
            )

    client.portal.call(run)


async def _joint_completion_boundary(client, project, topic, *, cancelled):
    """Real three-authority close; fail only inside its actual commit event.

    Cancellation here is an event-listener injection, not a claim about driver
    cancellation or cancellation after commit. Gateway billing is not involved.
    """
    factory = client.test_request_factory
    seat = await own_turns._seat(factory, project)
    identity = replace(
        _identity(project, topic, receiver=seat),
        harness=CLAUDE_CODE,
        native_session_id="native-session",
    )
    ids = await _blocks(factory, project, topic)
    input_query = select(NativeInput).where(
        NativeInput.project_id == project,
        NativeInput.conversation_id == topic,
        NativeInput.recipient_handle == seat,
        NativeInput.input_id == identity.input_id,
        NativeInput.work_id == identity.work_id,
    )
    usage_query = select(ResourceUsage).where(ResourceUsage.turn_id == identity.work_id)
    timer_query = select(TimedDelivery).where(
        TimedDelivery.project_id == project,
        TimedDelivery.conversation_id == topic,
        TimedDelivery.recipient_handle == seat,
    )
    blocks_query = select(Block).where(Block.id.in_(ids))
    topic_query = select(Block).where(Block.conversation_id == topic)
    async with factory() as session:
        await register_input(session, identity, InputEffects(held_block_ids=ids))
        await record_receipt(
            session,
            InputReceipt(identity, "native_echo", execution_work_id=identity.work_id),
        )
        await session.commit()
        before_blocks = set(
            await session.scalars(
                select(Block.id).where(Block.conversation_id == topic)
            )
        )
        instances = list(
            await session.scalars(
                select(AgentInstance).where(AgentInstance.project_id == project)
            )
        )
        (instance_id,) = [
            row.id for row in instances if agent_instance_handle(row.id) == seat
        ]
        assert list(await session.scalars(usage_query)) == []
        assert list(await session.scalars(timer_query)) == []
    state = _state(identity)
    state.route = OWN_ROUTE
    resets = datetime.now(UTC) + timedelta(hours=2)
    refused = {
        "type": "rate_limit_event",
        "rate_limit_info": {
            "status": "rejected",
            "resetsAt": int(resets.timestamp()),
            "rateLimitType": "five_hour",
        },
    }
    result = own_turns._result(seat, refused, own_turns._finished(seat))
    assert not result.is_error
    assert result.input_work_completed
    assert result.session_id == identity.native_session_id
    assert result.harness == identity.harness == CLAUDE_CODE
    assert result.agent_handle == identity.recipient_handle == state.acting_agent
    assert state.work_id == identity.work_id
    assert own_turns.USAGE["output_tokens"] > 0
    chat = own_turns._chat(factory)
    closing = asyncio.current_task()
    reached = []

    def fail_commit(session):
        if asyncio.current_task() is not closing:
            return
        session.flush()
        row = session.scalars(input_query).one()
        assert row.echoed_at is not None and row.completed_at is not None
        assert set(row.released_block_ids) == {str(block_id) for block_id in ids}
        assert {consumed_turn(block) for block in session.scalars(blocks_query)} == {
            str(identity.work_id)
        }
        (usage,) = list(session.scalars(usage_query))
        assert usage.output_tokens == own_turns.USAGE["output_tokens"]
        assert usage.credits == 0
        (timer,) = list(session.scalars(timer_query))
        assert timer.agent_instance_id == instance_id and timer.receiver_id is None
        reached.append(True)
        if cancelled:
            raise asyncio.CancelledError("cancel inside completion commit event")
        session.execute(text("SELECT 1 / 0"))

    event.listen(Session, "before_commit", fail_commit)
    try:
        with pytest.raises(asyncio.CancelledError if cancelled else DBAPIError):
            await chat.turn_completion.close(state, result)
    finally:
        event.remove(Session, "before_commit", fail_commit)
    assert reached == [True], "the real joint effects must exist before failing commit"
    async with factory() as observer:
        row = (await observer.scalars(input_query)).one()
        assert row.echoed_at is not None and row.completed_at is None
        assert row.released_block_ids == []
        assert all(
            consumed_turn(block) is None
            for block in await observer.scalars(blocks_query)
        )
        assert await held_blocks(
            observer, project_id=project, topic_id=topic, recipient_handle=seat
        ) == set(ids)
        assert list(await observer.scalars(usage_query)) == []
        assert list(await observer.scalars(timer_query)) == []
        assert {
            block.id for block in await observer.scalars(topic_query)
        } == before_blocks

    # The exact result can be retried after rollback, with the real effects.
    # This is not a second successful close or an exactly-once billing claim.
    await chat.turn_completion.close(state, result)
    async with factory() as observer:
        row = (await observer.scalars(input_query)).one()
        assert row.completed_at is not None and row.echoed_at is not None
        assert set(row.released_block_ids) == {str(block_id) for block_id in ids}
        assert {
            consumed_turn(block) for block in await observer.scalars(blocks_query)
        } == {str(identity.work_id)}
        assert (
            await held_blocks(
                observer, project_id=project, topic_id=topic, recipient_handle=seat
            )
            == set()
        )
        (usage,) = list(await observer.scalars(usage_query))
        assert usage.credits == 0
        assert usage.output_tokens == own_turns.USAGE["output_tokens"]
        assert usage.input_tokens == sum(
            own_turns.USAGE[key]
            for key in (
                "input_tokens",
                "cache_creation_input_tokens",
                "cache_read_input_tokens",
            )
        )
        (timer,) = list(await observer.scalars(timer_query))
        assert timer.agent_instance_id == instance_id and timer.receiver_id is None
        assert timer.due_at >= resets
        assert timer.due_at <= resets + timedelta(minutes=5)
        waiting = [
            block
            for block in await observer.scalars(topic_query)
            if block.id not in before_blocks
        ]
        assert len(waiting) == 1
        assert "分钟后恢复" in waiting[0].content


def test_own_native_completion_commit_abort_rolls_back_inputs_usage_and_resume_together(
    client,
):
    project = uuid.UUID(_project(client, "joint completion SQL abort"))
    topic = uuid.UUID(_room(client, str(project), "joint own completion"))

    async def run():
        await _joint_completion_boundary(client, project, topic, cancelled=False)

    client.portal.call(run)


def test_settle_commit_cancellation_propagates_and_rolls_back_joint_effects(client):
    project = uuid.UUID(_project(client, "joint completion commit cancellation"))
    topic = uuid.UUID(_room(client, str(project), "joint own cancellation"))

    async def run():
        await _joint_completion_boundary(client, project, topic, cancelled=True)

    client.portal.call(run)
