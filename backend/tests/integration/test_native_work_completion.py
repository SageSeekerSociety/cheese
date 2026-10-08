"""Completion transaction evidence; native-shaped result, not a live executor.

Registration/echo are real PostgreSQL operations. Results pass through the
Claude assembler and real ChatService work-close transaction. No provider or
ComputePool is restarted in these tests.
"""

import asyncio
import uuid
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from sqlalchemy import event, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.domain.agent.chat import ChatService
from app.domain.agent.harness import CLAUDE_CODE
from app.domain.agent.harness.claude_code.events import Assembler
from app.domain.agent.live_work import HookWorkState, LiveWork
from app.domain.block.models import Block, consumed_turn
from app.domain.block.repositories import BlockRepository
from app.domain.delivery.input_identity import InputEffects, InputReceipt
from app.domain.delivery.models import NativeInput
from app.domain.delivery.receipts import held_blocks, record_receipt, register_input
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
    return Assembler({}, session_id=identity.native_session_id).accept(record)[0]


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
        await chat._close_hook_work(state, result)
        await chat._close_hook_work(state, result)
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
            await chat._close_hook_work(state, result)
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
                await chat._close_hook_work(state, result)
        finally:
            event.remove(Session, "before_commit", fail_commit)
        assert aborted == [True]
        async with factory() as session:
            row = await session.scalar(select(NativeInput))
            assert row.released_block_ids == []
            blocks = list(await session.scalars(select(Block).where(Block.id.in_(ids))))
            assert all(consumed_turn(block) is None for block in blocks)
        await chat._close_hook_work(state, result)
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
