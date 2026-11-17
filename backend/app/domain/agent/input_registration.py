"""Persist a native input before I/O and retain its in-process liveness probe."""

import time

from app.core.errors import ValidationError
from app.domain.agent.turn.state.live import LiveWork
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
    started = None
    async with sessions() as session:
        row = await record_receipt(session, receipt)
        if row is None:
            # Unknown evidence cannot settle another input. Keep it replayable
            # rather than advancing the journal past a missing registration.
            raise ValidationError("Native receipt identity is unknown or conflicts")
        if receipt.evidence == "native_echo" and row.settled_at is not None:
            from app.domain.agent.turn_resume import started_on_receipt

            started = await started_on_receipt(session, row.delivery_id, row.attempt_id)
        await session.commit()
    if started is not None:
        from app.domain.agent.run_records import publish

        payload, room = started
        await publish(payload, room=room)
    if receipt.evidence != "native_echo":
        return
    pending = live.unread_inputs.get(receipt.identity.conversation_id)
    if pending is not None:
        pending.pop(receipt.identity.input_id, None)
