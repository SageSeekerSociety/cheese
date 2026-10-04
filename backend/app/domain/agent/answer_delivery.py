"""Offer a durable Ask answer to its executor's currently running work."""

import logging
from contextlib import asynccontextmanager

from sqlalchemy import select

from app.domain.agent.platform_notices import ask_answer_undelivered_notice
from app.domain.agent.seat_admission import seat_admission
from app.domain.block.models import Block
from app.domain.delivery.answer_ownership import (
    reconcile_answer,
    seat_has_unfinished_input,
)
from app.domain.delivery.ask_session_wait import (
    abandon_conversation_wait,
    hold_for_conversation,
    pinned_conversation,
    release_conversation_wait,
    wait_expired,
)
from app.domain.delivery.input_identity import (
    InputEffects,
    InputOutcomeUnconfirmed,
    InputReconciliationPending,
)
from app.domain.delivery.models import Delivery

logger = logging.getLogger(__name__)


async def run_with_answer_offer(
    runner, chat, topic_id, delivery_id, attempt_id, content, work
):
    entered = False
    try:
        await runner._wait_to_start()
        await runner._wait_for_replay(chat, topic_id, attempt_id)
        async with chat.session_factory() as session:
            delivery = await session.get(Delivery, delivery_id)
            is_answer = delivery is not None and "answer_to" in delivery.payload
            instance_id = (
                delivery.agent_instance_id
                if delivery is not None and is_answer
                else None
            )
            pinned = pinned_conversation(delivery) if is_answer else None
        if is_answer:
            seat = await chat._turn_seat_handle(
                topic_id, recipient_instance_id=instance_id
            )
            if pinned is not None:
                # An answer may enter only the conversation that asked it. While
                # that one is gone there is no turn to run: this attempt is a
                # liveness question, the answer is held out of this seat's
                # prompts, and the row waits instead of retrying every 30 s
                # (``ask_session_wait``).
                harness, conversation = pinned
                if not chat._compute.holds_conversation(
                    topic_id, harness, conversation
                ):
                    # A wait that outlived its day ends here: the answer
                    # is failed and the room is told to say it again.
                    if wait_expired(delivery) and (
                        await abandon_conversation_wait(
                            chat.session_factory, delivery_id
                        )
                    ):
                        await _say_answer_undelivered(chat, topic_id)
                        return
                    await hold_for_conversation(
                        chat.session_factory, delivery_id, conversation
                    )
                    return
                await release_conversation_wait(chat.session_factory, delivery_id)
            # Same lock as prompt preparation. Recheck only after an earlier
            # prompt has installed its work/identity; never send from stale idle.
            async with seat_admission(chat._seat_lock_for(topic_id, seat)):
                offered = await offer_answer(
                    chat, topic_id, delivery_id, attempt_id, content
                )
                if offered is True or isinstance(offered, InputReconciliationPending):
                    return
        entered = True
        await work
    finally:
        if not entered:
            work.close()


async def _say_answer_undelivered(chat, topic_id) -> None:
    """Tell the room that an answer never reached the conversation that asked.

    Best effort: a notice that fails must not take the attempt with it — the
    row is already where this module wanted it. The line is written to the
    room, where the agent's next prompt reads it back through its
    ``agent_notice`` meta. It goes out as no live frame: only the runtime
    holds the broker, and this module is imported by it.
    """
    line, meta = ask_answer_undelivered_notice()
    try:
        await chat.post_system_event(topic_id, line, meta=meta)
    except Exception:  # noqa: BLE001 — the answer's fate is already settled
        logger.exception("answer-undelivered notice failed for %s", topic_id)


@asynccontextmanager
async def admitted_initial(
    chat,
    topic_id,
    delivery_id,
    attempt_id,
    content,
    *,
    user_block_id=None,
    recipient_instance_id=None,
    recipient_handle=None,
):
    """Every initial turn rechecks durable ownership after project admission."""
    async with chat.session_factory() as session:
        delivery = (
            await session.get(Delivery, delivery_id)
            if delivery_id is not None
            else None
        )
        instance_id = (
            delivery.agent_instance_id
            if delivery is not None
            else recipient_instance_id
        )
        is_answer = delivery is not None and "answer_to" in delivery.payload
    seat = await chat._turn_seat_handle(
        topic_id,
        user_block_id=user_block_id,
        recipient_instance_id=instance_id,
        recipient_handle=recipient_handle,
    )
    async with seat_admission(chat._seat_lock_for(topic_id, seat)):
        if is_answer:
            offered = await offer_answer(
                chat, topic_id, delivery_id, attempt_id, content
            )
            yield offered is True or isinstance(offered, InputReconciliationPending)
            return
        from app.domain.agent.queries import session_agent_in_room

        async with chat.session_factory() as session:
            if instance_id is not None:
                from app.core.errors import ValidationError
                from app.domain.identity.handles import agent_instance_handle
                from app.domain.topic_membership.services import TopicMemberService

                if agent_instance_handle(instance_id) not in await TopicMemberService(
                    session
                ).agent_handles(topic_id):
                    raise ValidationError(
                        "The addressed agent is no longer seated in this room"
                    )
            agent = await session_agent_in_room(session, topic_id, seat)
            acting = await chat._acting_handle(session, topic_id, agent)
            pending = await seat_has_unfinished_input(session, topic_id, acting)
            if pending and user_block_id is not None:
                from app.domain.agent.pending_messages import defer_message

                await defer_message(session, user_block_id)
            await session.commit()
        yield pending


async def offer_answer(chat, topic_id, delivery_id, attempt_id, content):
    async with chat.session_factory() as session:
        delivery = await session.get(Delivery, delivery_id)
        if delivery is None or "answer_to" not in delivery.payload:
            return False
        seat = delivery.recipient_handle
        if await reconcile_answer(session, delivery_id, attempt_id):
            await session.commit()
            return True
        blocks = list(
            await session.scalars(
                select(Block.id).where(
                    Block.topic_id == topic_id,
                    Block.meta["delivery_event_id"].as_string()
                    == str(delivery.event_id),
                )
            )
        )
    work = chat._consuming_work_id(
        topic_id,
        lambda state: state.acting_agent == seat,
        strict=True,
    )
    if work is None:
        async with chat.session_factory() as session:
            return await seat_has_unfinished_input(session, topic_id, seat)
    state = chat._hook_work[(topic_id, work)]
    effects = InputEffects(
        held_block_ids=tuple(blocks),
        seen_block_ids=tuple(blocks),
        seen_by=seat,
        delivery_id=delivery_id,
        attempt_id=attempt_id,
    )
    register = chat._input_registrar(effects, probe_unread=True, fence_delivery=True)

    try:
        delivered = await chat._compute.steer(
            topic_id,
            content,
            register_input=register,
            expected_work_id=work,
            agent_handle=state.agent_instance_handle or state.acting_agent,
            owes_reply=True,
        )
        if delivered:
            return True
        async with chat.session_factory() as session:
            return await seat_has_unfinished_input(session, topic_id, seat)
    except InputOutcomeUnconfirmed as exc:
        return InputReconciliationPending(exc.identity, exc.accepted)
