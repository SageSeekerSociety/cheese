"""Receiver identities and their durable receipt settlement.

RPC acceptance, native echo, and database settlement are different facts. Only
an echo for the registered receiver may settle blocks and an agent delivery.
Nothing here resubmits an input whose outcome is uncertain.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import delete, or_, select, true, update
from sqlalchemy.dialects.postgresql import insert

from app.core.errors import ValidationError
from app.domain.block.input_effects import apply_input_echo, mark_read_failed
from app.domain.block.models import Block
from app.domain.delivery.input_holds import unread_input_with_over_work
from app.domain.delivery.input_identity import InputEffects, InputIdentity, InputReceipt
from app.domain.delivery.models import Delivery, NativeInput, TimedDelivery
from app.domain.notification.models import NotificationType
from app.domain.thread.services import thread_opening

#: payload key carrying the message a person's `@` named this seat in.
_SUMMON_BLOCK = "blockId"


async def _mention_summon_block(session, identity, delivery) -> uuid.UUID | None:
    """The message this delivery's recipient was named in, when it may be
    marked read by them; None for every delivery that is not a person's mention.

    Only a person's mention: an agent's `@` is at most advisory (I13) and a
    system wake names no one. The read receipt goes to the seat the delivery is
    for — ``identity.recipient_handle``, the receiver that has to agree — so a
    room woken by one `@` over two teammates shows one 👀 per teammate rather
    than one for the whole room.
    """
    if delivery is None or delivery.type != NotificationType.MENTION.value:
        return None
    payload = delivery.payload or {}
    if payload.get("eventType") != "person_mention":
        return None
    raw = payload.get(_SUMMON_BLOCK)
    if not raw:
        return None
    try:
        block_id = uuid.UUID(str(raw))
    except ValueError:
        return None
    # The platform owns this mark; the message must be there for it to land on.
    if await session.get(Block, block_id) is None:
        return None
    return block_id


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
            or delivery.conversation_id != identity.conversation_id
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
            topic_id=identity.conversation_id,
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
    # A 支线's first input is the message it hangs under and its files, which
    # stay in the channel's main line: those blocks are the 支线's too.
    opening = {
        block.id for block in await thread_opening(session, identity.conversation_id)
    }
    if len(rows) != len(ids) or any(
        block.project_id != identity.project_id
        or (
            block.conversation_id != identity.conversation_id
            and block.id not in opening
        )
        for block in rows
    ):
        raise ValidationError("Input blocks do not belong to the addressed room")
    return rows


async def held_blocks(
    session, *, project_id, topic_id, recipient_handle, exclude_input_id=None
):
    """The blocks inputs still hold: a batch is held from the moment its input
    is registered until the session reads it (the echo consumes and releases
    it, `record_receipt`) or its runner says it never will (`drop_input`). An
    input given up on with its fate unknown keeps holding its batch, so nothing
    the session may have read is offered again.

    One row is not a hold: an input nobody ever read whose work the platform has
    already ended (:func:`unread_input_with_over_work`). Nothing can settle its
    outcome, so continuing to hide its batch is what would lose it — the next
    prompt has to be able to carry it again (#416).
    """
    batches = (
        await session.execute(
            select(NativeInput.held_block_ids, NativeInput.released_block_ids).where(
                NativeInput.project_id == project_id,
                NativeInput.conversation_id == topic_id,
                NativeInput.recipient_handle == recipient_handle,
                NativeInput.id != exclude_input_id
                if exclude_input_id is not None
                else true(),
                ~unread_input_with_over_work(),
            )
        )
    ).all()
    return {
        uuid.UUID(block)
        for batch, released in batches
        for block in set(batch) - set(released)
    }


async def inputs_answered_inside(session, topic_id, work_id) -> list[uuid.UUID]:
    """The works whose input the session read inside ``work_id`` and answered
    there: each echoed under ``work_id`` though it is a work of its own."""
    return list(
        await session.scalars(
            select(NativeInput.work_id)
            .distinct()
            .where(
                NativeInput.conversation_id == topic_id,
                NativeInput.execution_work_id == work_id,
                NativeInput.work_id != work_id,
                NativeInput.echoed_at.is_not(None),
            )
        )
    )


async def withdraw_input(session, identity: InputIdentity, effects: InputEffects):
    """Undo a registration whose send stopped before anything left.

    The input never reached the session, so it is not a terminal outcome to
    record but a registration that should not exist: the row goes, which frees
    the seat and the blocks it would have held, and a fenced delivery attempt
    goes back to ``claimed`` so its attempt ends as an ordinary retry instead
    of an outcome nobody may repeat. A row the session has already answered
    for (accepted, echoed or settled) is evidence and stays."""
    await session.execute(
        delete(NativeInput).where(
            NativeInput.harness == identity.harness,
            NativeInput.native_session_id == identity.native_session_id,
            NativeInput.input_id == identity.input_id,
            NativeInput.accepted_at.is_(None),
            NativeInput.echoed_at.is_(None),
            NativeInput.terminated_at.is_(None),
        )
    )
    if effects.delivery_id is not None:
        await session.execute(
            update(Delivery)
            .where(
                Delivery.id == effects.delivery_id,
                Delivery.attempt_id == effects.attempt_id,
                Delivery.state == "sending",
            )
            .values(state="claimed")
        )


async def drop_input(
    session, *, harness, native_session_id, input_id, reason
) -> NativeInput | None:
    """The runner says the input will never be read: the build settled it
    unread, or its process ended with it unread.

    That is the one way an input written to a session comes back to the
    platform. The seat stops waiting on it, the blocks it held are released
    (nothing read them, so offering them again repeats nothing), and a delivery
    attempt it carried goes back to be retried. An input that was read is
    delivered and stays so; one settled already keeps its outcome.
    """
    row = await session.scalar(
        select(NativeInput)
        .where(
            NativeInput.harness == harness,
            NativeInput.native_session_id == native_session_id,
            NativeInput.input_id == input_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None or row.echoed_at is not None or row.terminated_at is not None:
        return None
    row.terminated_at = datetime.now(UTC)
    row.termination = reason
    row.released_block_ids = sorted(
        set(row.held_block_ids) | set(row.released_block_ids)
    )
    if row.delivery_id is not None:
        await session.execute(
            update(Delivery)
            .where(
                Delivery.id == row.delivery_id,
                Delivery.attempt_id == row.attempt_id,
                Delivery.state == "sending",
            )
            .values(state="claimed")
        )
    return row


async def drop_unread_of_session(session, *, harness, native_session_id, reason) -> int:
    """Every input written to this native session that it has neither read
    nor been reported never to read, dropped as its runner would drop it
    (``drop_input``): the process that held them was replaced, and nothing
    will read them. Their blocks are offered again."""
    unread = list(
        await session.scalars(
            select(NativeInput.input_id).where(
                NativeInput.harness == harness,
                NativeInput.native_session_id == native_session_id,
                NativeInput.echoed_at.is_(None),
                NativeInput.terminated_at.is_(None),
            )
        )
    )
    dropped = 0
    for input_id in unread:
        if (
            await drop_input(
                session,
                harness=harness,
                native_session_id=native_session_id,
                input_id=input_id,
                reason=reason,
            )
            is not None
        ):
            dropped += 1
    return dropped


async def read_inputs_of_session(
    session, *, harness, native_session_id, input_ids, work_id: uuid.UUID | None
) -> int:
    """These inputs of this native session, not yet recorded as read nor
    dropped, recorded as read, into ``work_id`` when it names one: its runner
    reported its works read them, in the one report this release can read of
    a runner another release started (its ping's ``endings``,
    ``room.sessions.lifecycle``). Read is delivered: nothing in them is
    offered again."""
    wanted = [uuid.UUID(str(input_id)) for input_id in input_ids]
    unread = list(
        await session.scalars(
            select(NativeInput).where(
                NativeInput.harness == harness,
                NativeInput.native_session_id == native_session_id,
                NativeInput.input_id.in_(wanted),
                NativeInput.echoed_at.is_(None),
                NativeInput.terminated_at.is_(None),
            )
        )
    )
    read = 0
    for row in unread:
        identity = InputIdentity(
            *(getattr(row, field) for field in InputIdentity.__dataclass_fields__)
        )
        if (
            await record_receipt(
                session,
                InputReceipt(
                    identity,
                    "native_echo",
                    execution_work_id=row.execution_work_id or work_id,
                ),
            )
            is not None
        ):
            read += 1
    return read


async def fail_read_inputs(
    session, *, harness, native_session_id, input_ids, failure
) -> int:
    """The work these inputs were read into failed: say so on each of them, and
    on the messages they carried (``READ_FAILED_META_KEY``).

    They stay delivered — a read input is — and nothing about them is offered
    again. What this records is that somebody's words were read and then got
    no answer, which is what a task close and an edit ask about.
    """
    ids = list(input_ids)
    if not ids:
        return 0
    rows = list(
        await session.scalars(
            select(NativeInput)
            .where(
                NativeInput.harness == harness,
                NativeInput.native_session_id == native_session_id,
                NativeInput.input_id.in_(ids),
                NativeInput.echoed_at.is_not(None),
                NativeInput.failed_at.is_(None),
            )
            .with_for_update()
        )
    )
    stamp = datetime.now(UTC)
    blocks: set[uuid.UUID] = set()
    for row in rows:
        row.failed_at = stamp
        row.failure = failure
        blocks |= {uuid.UUID(block) for block in row.held_block_ids + row.block_ids}
    await mark_read_failed(session, sorted(blocks), failure)
    return len(rows)


async def terminate_inputs_of_dead_works(session, work_ids, *, reason) -> int:
    """Give up on every unread input of these works whose fate nobody reported.

    For works the platform has established are dead, by the orphan sweep or a
    task closing: no runner will say any more whether they were read, and
    without this they would refuse the seat every later message. An input
    belongs to a work if it was sent into it or taken in it.

    Only ``terminated_at`` / ``termination`` are written: unlike an input the
    runner dropped (:func:`drop_input`), these may have been read, so their
    holds stay held and nothing inside them is sent again.
    """
    ids = list(work_ids)
    if not ids:
        return 0
    result = await session.execute(
        update(NativeInput)
        .where(
            or_(
                NativeInput.work_id.in_(ids),
                NativeInput.execution_work_id.in_(ids),
            ),
            NativeInput.echoed_at.is_(None),
            NativeInput.terminated_at.is_(None),
        )
        .values(terminated_at=datetime.now(UTC), termination=reason)
    )
    return result.rowcount or 0  # type: ignore[attr-defined]


async def terminate_unread_of_session(
    session, *, harness, native_session_id, reason
) -> int:
    """Give up on every unread input written into this native session.

    For a session whose machine the platform destroyed: the runner that would
    have reported any of them read or dropped went with the machine, so no
    later fact about them can arrive, and an unfinished input refuses its seat
    every later message (``seat_has_unfinished_input``).

    Keyed by the session rather than by its works, because the input that
    leaks is exactly the one no open work accounts for: a mid-turn addition
    whose own turn had already stopped when the machine went. Nothing in the
    works' close reaches it — that only names the turns still open.

    Only ``terminated_at`` / ``termination`` are written, as
    :func:`terminate_inputs_of_dead_works` does: the machine's death says
    nothing about whether they were read before it, so their holds stay held
    and nothing inside them is sent again.
    """
    result = await session.execute(
        update(NativeInput)
        .where(
            NativeInput.harness == harness,
            NativeInput.native_session_id == native_session_id,
            NativeInput.echoed_at.is_(None),
            NativeInput.terminated_at.is_(None),
        )
        .values(terminated_at=datetime.now(UTC), termination=reason)
    )
    return result.rowcount or 0  # type: ignore[attr-defined]


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
    if (
        execution_work is not None
        and row.execution_work_id not in (None, execution_work)
        and row.execution_work_id != identity.work_id
    ):
        # Two work stamps that disagree. The input's own work is exempt: that
        # value is the provisional default the bare-echo path below writes, not
        # a stamp the runner proved, so real evidence naming the executing work
        # (a retained interval completed under an adopted work) supersedes it.
        raise ValidationError("Native input has a different execution owner")
    if execution_work is None and row.execution_work_id is None:
        # An echo that names no execution work is an echo under the input's own
        # work: that is the one ``identity.work_id`` names, and the work the
        # echo below consumes the input's batch under.
        execution_work = identity.work_id
    if row.settled_at is not None:
        if execution_work is not None:
            row.execution_work_id = execution_work
        return row
    if row.delivery_id is not None and (
        delivery is None
        or delivery.event_id != row.event_id
        or delivery.attempt_id != row.attempt_id
        or delivery.conversation_id != row.conversation_id
        or delivery.recipient_handle != row.recipient_handle
        or delivery.state not in ("sending", "uncertain", "received")
    ):
        return None
    affected = {
        uuid.UUID(block)
        for block in (row.held_block_ids + row.block_ids + row.seen_block_ids)
    }
    # A teammate woken by a mention reads the message that named it, so it owes
    # that message the same read receipt a summoned seat owes its own. The
    # message is on the delivery, and this is where the delivery is settled.
    summon = await _mention_summon_block(session, identity, delivery)
    if summon is not None:
        affected.add(summon)
    await _lock_blocks(session, identity, affected)
    if row.seen_by is not None and row.seen_by != identity.recipient_handle:
        raise ValidationError("Input cannot mark blocks as read by another receiver")
    row.echoed_at = row.echoed_at or stamp
    row.execution_work_id = execution_work
    seen_ids = [uuid.UUID(block) for block in row.seen_block_ids]
    if summon is not None:
        seen_ids.append(summon)
    # Read is delivered: the batch the input held is consumed with it, and the
    # hold released (`held_blocks`).
    held = set(row.held_block_ids) - set(row.released_block_ids)
    row.released_block_ids = sorted(set(row.released_block_ids) | held)
    await apply_input_echo(
        session,
        consumed_ids=[
            uuid.UUID(block) for block in dict.fromkeys(row.block_ids + sorted(held))
        ],
        work_id=execution_work,
        seen_ids=seen_ids,
        seen_by=row.seen_by or row.recipient_handle,
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
