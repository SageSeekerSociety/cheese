"""Offer a durable Ask answer to its executor's currently running work."""

from contextlib import asynccontextmanager

from sqlalchemy import select

from app.domain.agent.seat_admission import seat_admission
from app.domain.block.models import Block
from app.domain.delivery.answer_ownership import (
    reconcile_answer,
    seat_has_unfinished_input,
)
from app.domain.delivery.input_identity import (
    InputEffects,
    InputOutcomeUnconfirmed,
    InputReconciliationPending,
)
from app.domain.delivery.models import Delivery


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
            instance_id = delivery.agent_instance_id if is_answer else None
        if is_answer:
            seat = await chat._turn_seat_handle(
                topic_id, recipient_instance_id=instance_id
            )
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


@asynccontextmanager
async def admitted_answer(chat, topic_id, delivery_id, attempt_id, content):
    """Project admission precedes this seat lock and the final pre-turn decision."""
    if delivery_id is None:
        yield False
        return
    async with chat.session_factory() as session:
        delivery = await session.get(Delivery, delivery_id)
        instance_id = delivery.agent_instance_id if delivery is not None else None
        is_answer = delivery is not None and "answer_to" in delivery.payload
    if not is_answer:
        yield False
        return
    seat = await chat._turn_seat_handle(topic_id, recipient_instance_id=instance_id)
    async with seat_admission(chat._seat_lock_for(topic_id, seat)):
        offered = await offer_answer(chat, topic_id, delivery_id, attempt_id, content)
        yield offered is True or isinstance(offered, InputReconciliationPending)


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
        topic_id, lambda state: state.acting_agent == seat, strict=True
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
        delivered = await chat._compute.deliver(
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
