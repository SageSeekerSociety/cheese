"""Persist a native input before I/O and retain its in-process liveness probe."""

import time
import uuid

from app.core.errors import ValidationError
from app.domain.agent.live_work import LiveWork
from app.domain.agent.realtime.broker import get_broker
from app.domain.block.queries import reaction_summaries_for_blocks
from app.domain.delivery.agent import DeliveryTargetChanged, fence_send
from app.domain.delivery.input_identity import (
    InputEffects,
    InputIdentity,
    InputRegistrar,
)
from app.domain.delivery.receipts import (
    record_receipt,
    register_input,
    withdraw_input,
)


def input_registrar(
    session_factory,
    effects: InputEffects,
    live: LiveWork,
    *,
    probe_unread: bool = False,
    fence_delivery: bool = False,
) -> InputRegistrar:
    return _Registration(session_factory, effects, live, probe_unread, fence_delivery)


class _Registration:
    def __init__(self, session_factory, effects, live, probe_unread, fence_delivery):
        self.session_factory = session_factory
        self.effects = effects
        self.live = live
        self.probe_unread = probe_unread
        self.fence_delivery = fence_delivery

    async def __call__(self, identity: InputIdentity) -> None:
        effects = self.effects
        rejected: DeliveryTargetChanged | None = None
        async with self.session_factory() as session:
            if self.fence_delivery and effects.delivery_id is not None:
                try:
                    await fence_send(session, effects.delivery_id, effects.attempt_id)
                except DeliveryTargetChanged as exc:
                    rejected = exc
            if rejected is None:
                await register_input(session, identity, effects)
            await session.commit()
        if rejected is not None:
            raise rejected
        if self.probe_unread:
            self.live.unread_inputs.setdefault(identity.conversation_id, {}).setdefault(
                identity.input_id, time.monotonic()
            )

    async def withdraw(self, identity: InputIdentity) -> None:
        async with self.session_factory() as session:
            await withdraw_input(session, identity, self.effects)
            await session.commit()
        pending = self.live.unread_inputs.get(identity.conversation_id)
        if pending is not None:
            pending.pop(identity.input_id, None)


async def confirm_receipt(sessions, live: LiveWork, receipt) -> None:
    async with sessions() as session:
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
    pending = live.unread_inputs.get(receipt.identity.conversation_id)
    if pending is not None:
        pending.pop(receipt.identity.input_id, None)

    for block_id, value in reactions.items():
        await get_broker().publish(
            str(receipt.identity.conversation_id),
            {"type": "reaction", "block_id": str(block_id), "reactions": value},
        )
