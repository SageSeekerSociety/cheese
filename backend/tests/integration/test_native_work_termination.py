"""A work interval proved dead frees the seat, and frees nothing else.

The bug this covers: ``completed_at`` has one writer, and only a clean native
result reaches it. An error, or a Stop, never does — so the row stayed
unfinished forever and the seat stopped accepting anything, with the room
showing "started" and nothing ever sent.

The fix records the terminal outcome on columns of its own. It must never look
like completion: whether the answer inside those rows was taken is still
unknown, so the holds stay and nothing is re-sent.
"""

import uuid

import pytest
from sqlalchemy import select

from app.core.errors import ValidationError
from app.domain.block.models import AuthorType, Block, BlockKind
from app.domain.delivery.answer_ownership import seat_has_unfinished_input
from app.domain.delivery.input_identity import (
    InputEffects,
    InputIdentity,
    InputReceipt,
)
from app.domain.delivery.models import NativeInput
from app.domain.delivery.receipts import (
    complete_work_inputs,
    held_blocks,
    record_receipt,
    register_input,
    terminate_work_inputs,
)
from tests.integration.test_same_handle_note_and_timed_delivery import _project, _room


def _identity(**over) -> InputIdentity:
    values = {
        "project_id": uuid.uuid4(),
        "conversation_id": uuid.uuid4(),
        "recipient_handle": "cheese-test",
        "harness": "claude_code",
        "native_session_id": str(uuid.uuid4()),
        "input_id": uuid.uuid4(),
        "work_id": uuid.uuid4(),
    }
    values.update(over)
    return InputIdentity(**values)


async def _blocks(factory, project_id, topic_id, count=2):
    ids = tuple(uuid.uuid4() for _ in range(count))
    async with factory() as session:
        for block_id in ids:
            session.add(
                Block(
                    id=block_id,
                    project_id=project_id,
                    conversation_id=topic_id,
                    kind=BlockKind.message,
                    author_type=AuthorType.participant,
                    author="user-1",
                    content="answer",
                    meta={"consumed_turn": None},
                )
            )
        await session.commit()
    return ids


async def _row(factory, identity) -> NativeInput:
    async with factory() as session:
        return await session.scalar(
            select(NativeInput).where(NativeInput.input_id == identity.input_id)
        )


async def _echo(factory, identity) -> None:
    """The session took this input — the receipt that names its execution work.

    Only an echoed input is journaled under ``execution_inputs:{work}``, which
    is the interval list both completion and termination read. Registering
    alone leaves the execution owner empty, and that is the honest state of an
    input nobody has taken yet.
    """
    async with factory() as session:
        await record_receipt(
            session, InputReceipt(identity, "native_echo", identity.work_id)
        )
        await session.commit()


async def _terminate(factory, identity, *, reason, input_ids=None):
    async with factory() as session:
        touched = await terminate_work_inputs(
            session,
            project_id=identity.project_id,
            conversation_id=identity.conversation_id,
            recipient_handle=identity.recipient_handle,
            harness=identity.harness,
            native_session_id=identity.native_session_id,
            work_id=identity.work_id,
            input_ids=input_ids or (identity.input_id,),
            reason=reason,
        )
        await session.commit()
    return touched


async def test_a_confirmed_termination_frees_the_seat_but_keeps_the_answer_unknown(
    client,
):
    project = uuid.UUID(_project(client, "work termination"))
    topic = uuid.UUID(_room(client, str(project), "a dead work"))

    async def run():
        factory = client.test_request_factory
        identity = _identity(project_id=project, conversation_id=topic)
        held = await _blocks(factory, identity.project_id, identity.conversation_id)
        async with factory() as session:
            await register_input(session, identity, InputEffects(held_block_ids=held))
            await session.commit()
        await _echo(factory, identity)
        async with factory() as session:
            assert await seat_has_unfinished_input(
                session, identity.conversation_id, identity.recipient_handle
            )

        assert await _terminate(factory, identity, reason="is_error") == {
            identity.input_id
        }

        row = await _row(factory, identity)
        # It is not a completion.
        assert row.completed_at is None
        assert row.terminated_at is not None
        assert row.termination == "is_error"
        # And nothing says the answer inside it was taken: the holds are still
        # held and never released, so the answer cannot be sent a second time.
        assert row.held_block_ids == [str(block) for block in held]
        assert row.released_block_ids == []
        assert row.block_ids == []
        async with factory() as session:
            assert await held_blocks(
                session,
                project_id=identity.project_id,
                topic_id=identity.conversation_id,
                recipient_handle=identity.recipient_handle,
            ) == set(held)

        # The seat is free again: a NEW input has an identity of its own.
        async with factory() as session:
            assert not await seat_has_unfinished_input(
                session, identity.conversation_id, identity.recipient_handle
            )
        later = _identity(
            project_id=identity.project_id,
            conversation_id=identity.conversation_id,
            recipient_handle=identity.recipient_handle,
        )
        async with factory() as session:
            await register_input(session, later, InputEffects())
            await session.commit()
        assert await _row(factory, later) is not None

    client.portal.call(run)


async def test_an_outcome_nobody_confirmed_still_blocks_the_seat(client):
    async def run():
        factory = client.test_request_factory
        # The bug's own shape: the session took the input, and then the work
        # ended in a way nobody has confirmed. Nothing may free the seat.
        echoed = _identity()
        async with factory() as session:
            await register_input(session, echoed, InputEffects())
            await session.commit()
        await _echo(factory, echoed)
        async with factory() as session:
            assert await seat_has_unfinished_input(
                session, echoed.conversation_id, echoed.recipient_handle
            )

        # Nor does an input nobody has taken yet.
        untouched = _identity()
        async with factory() as session:
            await register_input(session, untouched, InputEffects())
            await session.commit()
        async with factory() as session:
            assert await seat_has_unfinished_input(
                session, untouched.conversation_id, untouched.recipient_handle
            )

    client.portal.call(run)


async def test_a_termination_touches_only_its_own_interval_and_seat(client):
    async def run():
        factory = client.test_request_factory
        topic_id = uuid.uuid4()
        mine = _identity(conversation_id=topic_id, recipient_handle="cheese-test")
        other_seat = _identity(
            conversation_id=topic_id, recipient_handle="cheese-other"
        )
        later_interval = _identity(
            project_id=mine.project_id,
            conversation_id=topic_id,
            recipient_handle="cheese-test",
        )
        for identity in (mine, other_seat, later_interval):
            async with factory() as session:
                await register_input(session, identity, InputEffects())
                await session.commit()
            await _echo(factory, identity)

        await _terminate(factory, mine, reason="interrupted")

        mine_row = await _row(factory, mine)
        assert mine_row.terminated_at is not None
        assert mine_row.termination == "interrupted"
        for untouched in (other_seat, later_interval):
            row = await _row(factory, untouched)
            assert row.terminated_at is None
            assert row.termination is None

        # The seat still owes that later interval, and the other seat is not
        # this call's business at all.
        async with factory() as session:
            assert await seat_has_unfinished_input(
                session, mine.conversation_id, mine.recipient_handle
            )
            assert await seat_has_unfinished_input(
                session, other_seat.conversation_id, other_seat.recipient_handle
            )

    client.portal.call(run)


async def test_a_clean_completion_is_not_rewritten_by_a_termination(client):
    async def run():
        factory = client.test_request_factory
        identity = _identity()
        async with factory() as session:
            await register_input(session, identity, InputEffects())
            await session.commit()
        await _echo(factory, identity)
        async with factory() as session:
            await complete_work_inputs(
                session,
                project_id=identity.project_id,
                conversation_id=identity.conversation_id,
                recipient_handle=identity.recipient_handle,
                harness=identity.harness,
                native_session_id=identity.native_session_id,
                work_id=identity.work_id,
                input_ids=(identity.input_id,),
            )
            await session.commit()
        await _terminate(factory, identity, reason="is_error")

        row = await _row(factory, identity)
        assert row.completed_at is not None
        assert row.terminated_at is None
        assert row.termination is None

    client.portal.call(run)


async def test_a_termination_that_covers_half_an_interval_writes_nothing(client):
    async def run():
        factory = client.test_request_factory
        identity = _identity()
        second = _identity(
            project_id=identity.project_id,
            conversation_id=identity.conversation_id,
            recipient_handle=identity.recipient_handle,
            native_session_id=identity.native_session_id,
            work_id=identity.work_id,
        )
        for item in (identity, second):
            async with factory() as session:
                await register_input(session, item, InputEffects())
                await session.commit()
            await _echo(factory, item)

        # Naming only part of the interval would free inputs the same work may
        # still be live on. Refuse it, and write nothing at all.
        with pytest.raises(ValidationError):
            await _terminate(
                factory, identity, reason="is_error", input_ids=(identity.input_id,)
            )

        for item in (identity, second):
            row = await _row(factory, item)
            assert row.terminated_at is None
            assert row.termination is None

    client.portal.call(run)
