"""Receiver identities and their durable receipt settlement.

RPC acceptance, native echo, and database settlement are different facts. Only
an echo for the registered receiver may settle blocks and an agent delivery.
Nothing here resubmits an input whose outcome is uncertain.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.core.errors import ValidationError
from app.domain.block.repositories import BlockRepository
from app.domain.delivery.models import Delivery, NativeInput, TimedDelivery


@dataclass(frozen=True)
class InputIdentity:
    project_id: uuid.UUID
    topic_id: uuid.UUID
    recipient_handle: str
    harness: str
    native_session_id: str
    input_id: uuid.UUID
    work_id: uuid.UUID


@dataclass(frozen=True)
class InputReceipt:
    identity: InputIdentity
    evidence: Literal["accepted", "native_echo"]


@dataclass(frozen=True)
class InputEffects:
    block_ids: tuple[uuid.UUID, ...] = ()
    seen_block_ids: tuple[uuid.UUID, ...] = ()
    seen_by: str | None = None
    delivery_id: uuid.UUID | None = None
    attempt_id: uuid.UUID | None = None


def _same_receiver(row: NativeInput, identity: InputIdentity) -> bool:
    return all(
        getattr(row, field) == getattr(identity, field)
        for field in InputIdentity.__dataclass_fields__
    )


async def register_input(
    session, identity: InputIdentity, effects: InputEffects
) -> None:
    """Caller commits this before sending; retry cannot replace receiver identity."""
    event_id = None
    if effects.delivery_id is not None:
        delivery = await session.scalar(
            select(Delivery)
            .where(Delivery.id == effects.delivery_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if (
            delivery is None
            or delivery.attempt_id != effects.attempt_id
            or delivery.topic_id != identity.topic_id
            or delivery.recipient_handle != identity.recipient_handle
            or delivery.state != "sending"
        ):
            raise ValidationError("Input does not own the addressed delivery attempt")
        event_id = delivery.event_id
    elif effects.attempt_id is not None:
        raise ValidationError("An attempt must name its delivery")
    values = {
        **{
            field: getattr(identity, field)
            for field in InputIdentity.__dataclass_fields__
        },
        "delivery_id": effects.delivery_id,
        "attempt_id": effects.attempt_id,
        "event_id": event_id,
        "block_ids": [str(block) for block in effects.block_ids],
        "seen_block_ids": [str(block) for block in effects.seen_block_ids],
        "seen_by": effects.seen_by,
    }
    await session.execute(
        insert(NativeInput)
        .values(id=uuid.uuid4(), registered_at=datetime.now(UTC), **values)
        .on_conflict_do_nothing(constraint="uq_native_input_identity")
    )
    row = await session.scalar(
        select(NativeInput).where(
            NativeInput.harness == identity.harness,
            NativeInput.native_session_id == identity.native_session_id,
            NativeInput.input_id == identity.input_id,
        )
    )
    if row is None or any(getattr(row, key) != value for key, value in values.items()):
        raise ValidationError("Native input identity was reused for another input")


async def record_receipt(session, receipt: InputReceipt) -> NativeInput | None:
    """Reject unknown/conflicting identity without selecting by prompt text.

    A failed commit propagates to the journal reader. Re-reading an echo after a
    restart repeats this transaction safely, without any in-memory candidates.
    """
    identity = receipt.identity
    row = await session.scalar(
        select(NativeInput)
        .where(
            NativeInput.harness == identity.harness,
            NativeInput.native_session_id == identity.native_session_id,
            NativeInput.input_id == identity.input_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None or not _same_receiver(row, identity):
        return None
    stamp = datetime.now(UTC)
    if receipt.evidence == "accepted":
        row.accepted_at = row.accepted_at or stamp
        return row
    if receipt.evidence != "native_echo":
        return None
    row.echoed_at = row.echoed_at or stamp
    if row.settled_at is not None:
        return row
    delivery = None
    if row.delivery_id is not None:
        delivery = await session.scalar(
            select(Delivery)
            .where(Delivery.id == row.delivery_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if (
            delivery is None
            or delivery.event_id != row.event_id
            or delivery.attempt_id != row.attempt_id
            or delivery.topic_id != row.topic_id
            or delivery.recipient_handle != row.recipient_handle
            or delivery.state not in ("sending", "uncertain", "received")
        ):
            return None
    await BlockRepository(session).mark_consumed(
        [uuid.UUID(block) for block in row.block_ids], row.work_id
    )
    if delivery is not None:
        delivery.state = "received"
        delivery.sent_at = stamp
        delivery.lease_until = None
        delivery.last_error = None
        timers = await session.scalars(
            select(TimedDelivery).where(
                TimedDelivery.event_id == row.event_id,
                TimedDelivery.delivered_at.is_(None),
            )
        )
        for timer in timers:
            timer.delivered_at = stamp
    row.settled_at = stamp
    return row
