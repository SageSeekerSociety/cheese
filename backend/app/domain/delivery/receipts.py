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
from app.domain.block.models import Block
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
    # INSERT/unique-index conflict can wait on another input transaction. Do it
    # before Block locks, so neither admission nor settlement has a Block→Input
    # edge. Validation failure requires caller rollback of this whole transaction.
    inserted = await session.scalar(
        insert(NativeInput)
        .values(id=uuid.uuid4(), registered_at=datetime.now(UTC), **values)
        .on_conflict_do_nothing(constraint="uq_native_input_identity")
        .returning(NativeInput.id)
    )
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
    if row is None or any(getattr(row, key) != value for key, value in values.items()):
        raise ValidationError("Native input identity was reused for another input")
    if inserted is None:
        return
    affected = (
        set(effects.held_block_ids)
        | set(effects.block_ids)
        | set(effects.seen_block_ids)
    )
    await _lock_blocks(session, identity, affected)
    if effects.seen_by is not None and effects.seen_by != identity.recipient_handle:
        raise ValidationError("Input cannot mark blocks as read by another receiver")
    if effects.held_block_ids:
        held = await held_blocks(
            session,
            project_id=identity.project_id,
            topic_id=identity.topic_id,
            recipient_handle=identity.recipient_handle,
            exclude_input_id=row.id,
        )
        if held.intersection(effects.held_block_ids):
            raise ValidationError("Input batch is already held by another native input")


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


async def held_blocks(
    session, *, project_id, topic_id, recipient_handle, exclude_input_id=None
):
    """Registered inputs own their batch, independently of work completion.

    An echo cannot release an initial batch for another prompt before its Stop.
    Re-admission requires explicit reconciliation, never a different input UUID.
    """
    batches = (
        await session.execute(
            select(NativeInput.held_block_ids, NativeInput.released_block_ids).where(
                NativeInput.project_id == project_id,
                NativeInput.topic_id == topic_id,
                NativeInput.recipient_handle == recipient_handle,
                NativeInput.id != exclude_input_id
                if exclude_input_id is not None
                else True,
            )
        )
    ).all()
    return {
        uuid.UUID(block)
        for batch, released in batches
        for block in set(batch) - set(released)
    }


async def complete_work_inputs(
    session,
    *,
    project_id,
    topic_id,
    recipient_handle,
    harness,
    native_session_id,
    work_id,
    require_registered=False,
    input_ids=None,
):
    """Commit consumption and durable release for this exact successful work.

    Called from the clean native result transaction. Never infer completion from
    acceptance, an echo, another work's marker, or missing process-local state.
    """
    rows = list(
        await session.scalars(
            select(NativeInput)
            .where(
                NativeInput.project_id == project_id,
                NativeInput.topic_id == topic_id,
                NativeInput.recipient_handle == recipient_handle,
                NativeInput.harness == harness,
                NativeInput.native_session_id == native_session_id,
                NativeInput.execution_work_id == work_id,
                NativeInput.input_id.in_(input_ids) if input_ids is not None else True,
            )
            .order_by(NativeInput.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )
    if require_registered and (
        not rows
        or any(row.echoed_at is None for row in rows)
        or input_ids is None
        or {row.input_id for row in rows} != set(input_ids)
    ):
        raise ValidationError("Native completion has no confirmed registered work")
    owned = {
        uuid.UUID(block)
        for row in rows
        if row.echoed_at is not None
        for block in set(row.held_block_ids) - set(row.released_block_ids)
    }
    consumed = owned
    stamp = datetime.now(UTC)
    for row in rows:
        if row.echoed_at is not None:
            row.completed_at = row.completed_at or stamp
    if not consumed:
        return set()
    identity = InputIdentity(
        project_id,
        topic_id,
        recipient_handle,
        harness,
        native_session_id,
        rows[0].input_id if rows else work_id,
        work_id,
    )
    await _lock_blocks(session, identity, consumed)
    await BlockRepository(session).mark_consumed(list(consumed), work_id)
    for row in rows:
        if row.echoed_at is not None:
            released = set(row.released_block_ids) | (
                set(row.held_block_ids) & {str(block) for block in consumed}
            )
            row.released_block_ids = sorted(released)
    return consumed


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
    execution_work = receipt.execution_work_id
    if execution_work is not None and row.execution_work_id not in (
        None,
        execution_work,
    ):
        raise ValidationError("Native input has a different execution owner")
    if row.settled_at is not None:
        if execution_work is not None:
            row.execution_work_id = execution_work
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
    row.execution_work_id = execution_work
    blocks = BlockRepository(session)
    if execution_work is not None:
        await blocks.mark_consumed(
            [uuid.UUID(block) for block in row.block_ids], execution_work
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
