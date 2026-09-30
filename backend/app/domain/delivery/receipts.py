"""Receiver identities and their durable receipt settlement.

RPC acceptance, native echo, and database settlement are different facts. Only
an echo for the registered receiver may settle blocks and an agent delivery.
Nothing here resubmits an input whose outcome is uncertain.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.core.errors import ValidationError
from app.domain.block.models import Block, consumed_turn
from app.domain.block.repositories import BlockRepository
from app.domain.delivery.input_identity import InputEffects, InputIdentity, InputReceipt
from app.domain.delivery.models import Delivery, NativeInput, TimedDelivery


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
    delivery = None
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
        "held_block_ids": [str(block) for block in effects.held_block_ids],
        "block_ids": [str(block) for block in effects.block_ids],
        "seen_block_ids": [str(block) for block in effects.seen_block_ids],
        "seen_by": effects.seen_by,
    }
    existing = await session.scalar(
        select(NativeInput)
        .where(
            NativeInput.harness == identity.harness,
            NativeInput.native_session_id == identity.native_session_id,
            NativeInput.input_id == identity.input_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if existing is not None:
        if any(getattr(existing, key) != value for key, value in values.items()):
            raise ValidationError("Native input identity was reused for another input")
        return
    # A settled/uncertain attempt may verify an old registration, never add one.
    if delivery is not None and delivery.state != "sending":
        raise ValidationError("Input does not own the addressed delivery attempt")
    affected = (
        set(effects.held_block_ids)
        | set(effects.block_ids)
        | set(effects.seen_block_ids)
    )
    await _lock_blocks(session, identity, affected)
    if effects.seen_by is not None and effects.seen_by != identity.recipient_handle:
        raise ValidationError("Input cannot mark blocks as read by another receiver")
    # Another registration can commit while we wait for the shared block locks.
    existing = await session.scalar(
        select(NativeInput)
        .where(
            NativeInput.harness == identity.harness,
            NativeInput.native_session_id == identity.native_session_id,
            NativeInput.input_id == identity.input_id,
        )
        .execution_options(populate_existing=True)
    )
    if existing is not None:
        if any(getattr(existing, key) != value for key, value in values.items()):
            raise ValidationError("Native input identity was reused for another input")
        return
    if effects.held_block_ids:
        held = await held_blocks(
            session,
            project_id=identity.project_id,
            topic_id=identity.topic_id,
            recipient_handle=identity.recipient_handle,
        )
        if held.intersection(effects.held_block_ids):
            raise ValidationError("Input batch is already held by another native input")
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


async def _lock_blocks(session, identity: InputIdentity, ids: set[uuid.UUID]):
    if not ids:
        return []
    rows = list(
        await session.scalars(
            select(Block)
            .where(Block.id.in_(ids))
            .order_by(Block.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )
    if len(rows) != len(ids) or any(
        block.project_id != identity.project_id or block.topic_id != identity.topic_id
        for block in rows
    ):
        raise ValidationError("Input blocks do not belong to the addressed room")
    return rows


async def held_blocks(session, *, project_id, topic_id, recipient_handle):
    """Registered inputs own their batch, independently of work completion.

    An echo cannot release an initial batch for another prompt before its Stop.
    Re-admission requires explicit reconciliation, never a different input UUID.
    """
    batches = (
        await session.execute(
            select(NativeInput.held_block_ids, NativeInput.work_id).where(
                NativeInput.project_id == project_id,
                NativeInput.topic_id == topic_id,
                NativeInput.recipient_handle == recipient_handle,
            )
        )
    ).all()
    ids = {uuid.UUID(block) for batch, _ in batches for block in batch}
    if not ids:
        return ids
    rows = await session.scalars(select(Block).where(Block.id.in_(ids)))
    consumed = {block.id: consumed_turn(block) for block in rows}
    # Only consumption by this input's registered work releases its hold.
    # A different work's marker must not erase an uncertain input's ownership.
    return {
        uuid.UUID(block)
        for batch, work in batches
        for block in batch
        if consumed.get(uuid.UUID(block)) != str(work)
    }


async def record_receipt(session, receipt: InputReceipt) -> NativeInput | None:
    """Reject unknown/conflicting identity without selecting by prompt text.

    A failed commit propagates to the journal reader. Re-reading an echo after a
    restart repeats this transaction safely, without any in-memory candidates.
    """
    identity = receipt.identity
    # Discover the immutable link without locking NativeInput. Registration and
    # settlement both lock Delivery first, then re-read NativeInput under lock.
    link = (
        await session.execute(
            select(NativeInput.id, NativeInput.delivery_id).where(
                *(
                    getattr(NativeInput, field) == getattr(identity, field)
                    for field in InputIdentity.__dataclass_fields__
                )
            )
        )
    ).one_or_none()
    if link is None:
        return None
    delivery = None
    if link.delivery_id is not None:
        delivery = await session.scalar(
            select(Delivery)
            .where(Delivery.id == link.delivery_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    row = await session.scalar(
        select(NativeInput)
        .where(NativeInput.id == link.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if (
        row is None
        or not _same_receiver(row, identity)
        or row.delivery_id != link.delivery_id
    ):
        return None
    stamp = datetime.now(UTC)
    if receipt.evidence == "accepted":
        row.accepted_at = row.accepted_at or stamp
        return row
    if receipt.evidence != "native_echo":
        return None
    if row.settled_at is not None:
        return row
    if row.delivery_id is not None and (
        delivery is None
        or delivery.event_id != row.event_id
        or delivery.attempt_id != row.attempt_id
        or delivery.topic_id != row.topic_id
        or delivery.recipient_handle != row.recipient_handle
        or delivery.state not in ("sending", "uncertain", "received")
    ):
        return None
    affected = {
        uuid.UUID(block)
        for block in (row.held_block_ids + row.block_ids + row.seen_block_ids)
    }
    await _lock_blocks(session, identity, affected)
    if row.seen_by is not None and row.seen_by != identity.recipient_handle:
        raise ValidationError("Input cannot mark blocks as read by another receiver")
    row.echoed_at = row.echoed_at or stamp
    blocks = BlockRepository(session)
    await blocks.mark_consumed(
        [uuid.UUID(block) for block in row.block_ids], row.work_id
    )
    for block in row.seen_block_ids:
        await blocks.add_reaction_if_absent(
            uuid.UUID(block), "👀", row.seen_by or row.recipient_handle
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
