"""Persist a native input before I/O and retain its in-process liveness probe."""

import time
import uuid

from app.core.errors import ValidationError
from app.domain.block.queries import reaction_summaries_for_blocks
from app.domain.delivery.agent import DeliveryTargetChanged, fence_send
from app.domain.delivery.input_identity import (
    InputEffects,
    InputIdentity,
    InputRegistrar,
)
from app.domain.delivery.receipts import record_receipt, register_input


def input_registrar(
    session_factory,
    effects: InputEffects,
    unread_inputs,
    *,
    probe_unread: bool = False,
    fence_delivery: bool = False,
) -> InputRegistrar:
    async def persist(identity: InputIdentity) -> None:
        rejected: DeliveryTargetChanged | None = None
        async with session_factory() as session:
            if fence_delivery and effects.delivery_id is not None:
                try:
                    await fence_send(session, effects.delivery_id, effects.attempt_id)
                except DeliveryTargetChanged as exc:
                    rejected = exc
            if rejected is None:
                await register_input(session, identity, effects)
            await session.commit()
        if rejected is not None:
            raise rejected
        if probe_unread:
            unread_inputs.setdefault(identity.conversation_id, {}).setdefault(
                identity.input_id, time.monotonic()
            )

    return persist


async def confirm_receipt(chat, receipt) -> None:
    async with chat._sessions() as session:
        row = await record_receipt(session, receipt)
        if row is None:
            # Unknown evidence cannot settle another input. Keep it replayable
            # rather than advancing the journal past a missing registration.
            raise ValidationError("Native receipt identity is unknown or conflicts")
        seen_ids = [uuid.UUID(block) for block in row.seen_block_ids]
        reactions = (
            await reaction_summaries_for_blocks(session, seen_ids)
            if row.settled_at is not None
            else {}
        )
        await session.commit()
    if receipt.evidence != "native_echo":
        return
    # Echo commits before a waiting correction re-enters normal admission.
    chat.nudge_ask_receipts(receipt.identity)
    pending = chat._unread_inputs.get(receipt.identity.conversation_id)
    if pending is not None:
        pending.pop(receipt.identity.input_id, None)
    from app.domain.agent.runtime import get_broker

    for block_id, value in reactions.items():
        await get_broker().publish(
            str(receipt.identity.conversation_id),
            {"type": "reaction", "block_id": str(block_id), "reactions": value},
        )
