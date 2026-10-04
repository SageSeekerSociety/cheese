"""Reconcile one answer event against its durable input and consumption facts."""

from datetime import UTC, datetime

from sqlalchemy import and_, select

from app.domain.block.models import Block, consumed_turn
from app.domain.delivery.agent import work_interval_is_over
from app.domain.delivery.models import Delivery, NativeInput


async def reconcile_answer(session, delivery_id, attempt_id):
    """Return True to withhold I/O, False only for an unowned answer batch.

    An initial prompt can own an answer without owning its independent Delivery.
    Its retained block IDs identify that exact version even after hold release.
    Lock Delivery, then Input, then sorted Blocks, just like receipt settlement.
    """
    delivery = await session.scalar(
        select(Delivery)
        .where(Delivery.id == delivery_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if delivery is None or "answer_to" not in delivery.payload:
        return False
    if (
        delivery.attempt_id != attempt_id
        or delivery.state != "claimed"
        or delivery.lease_until is None
        or delivery.lease_until <= datetime.now(UTC)
    ):
        return True
    candidates = list(
        await session.scalars(
            select(Block).where(
                Block.topic_id == delivery.topic_id,
                Block.meta["delivery_event_id"].as_string() == str(delivery.event_id),
                Block.meta["answer_to"].as_string() == delivery.payload["answer_to"],
            )
        )
    )
    if not candidates:
        return True
    ids = {str(block.id) for block in candidates}
    project = candidates[0].project_id
    origin = delivery.payload.get("ask_origin")
    receiver = []
    if origin is not None:
        receiver = [
            NativeInput.harness == origin["harness"],
            NativeInput.native_session_id == origin["native_session_id"],
        ]
    inputs = list(
        await session.scalars(
            select(NativeInput)
            .where(
                NativeInput.project_id == project,
                NativeInput.topic_id == delivery.topic_id,
                NativeInput.recipient_handle == delivery.recipient_handle,
                *receiver,
            )
            .order_by(NativeInput.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )
    owners = {
        block_id: [
            row
            for row in inputs
            if block_id in row.held_block_ids or block_id in row.block_ids
        ]
        for block_id in ids
    }
    blocks = list(
        await session.scalars(
            select(Block)
            .where(Block.id.in_([block.id for block in candidates]))
            .order_by(Block.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )
    confirmed = []
    for block in blocks:
        matching = [
            row
            for row in owners[str(block.id)]
            if row.echoed_at is not None
            and row.execution_work_id is not None
            and consumed_turn(block) == str(row.execution_work_id)
            and (
                str(block.id) in row.block_ids
                or str(block.id) in row.released_block_ids
            )
        ]
        if len(matching) != 1:
            # Any durable owner or unexplained consumption forbids a fresh send.
            return any(owners.values()) or any(consumed_turn(b) for b in blocks)
        confirmed.append(matching[0])
    delivery.state = "received"
    delivery.sent_at = max(row.echoed_at for row in confirmed)
    delivery.lease_until = None
    delivery.last_error = None
    delivery.payload = {
        **delivery.payload,
        "consumed_answer_inputs": [str(row.id) for row in confirmed],
    }
    return True


def unread_input_with_over_work():
    """No native receipt can settle this input any more, so it may not hold.

    Only an echoed input is journaled under its execution work, and that
    journal is the interval completion and termination both read. An input the
    session never took therefore has no interval a receipt could name.

    An input's OWN work is the turn opened to carry it (``input_id`` names the
    same work): a turn that existed only to deliver this batch, ended by the
    platform with the batch never read, has nothing left to wait for — keeping
    its hold would swallow a person's message, which is the one thing #416 does
    not allow. A batch that was an addition to another work (a mid-turn
    delivery) is different: a transport error does not prove the working
    session did not read it, so its outcome stays unknown and it keeps holding
    and blocking the seat. Whether that work is over is the turn interval's
    fact (:func:`app.domain.delivery.agent.work_interval_is_over`) — a work the
    platform has no row for is not over, silence is not a conclusion.
    """
    return and_(
        NativeInput.echoed_at.is_(None),
        NativeInput.input_id == NativeInput.work_id,
        work_interval_is_over(),
    )


async def seat_has_unfinished_input(session, topic_id, recipient_handle):
    """Missing native start or outstanding holds cannot authorize a new send.

    A work interval that is confirmed dead is not an outstanding hold. Its rows
    stay unfinished — nothing says the answer inside them was taken — but they
    stop standing between the seat and a NEW input, which is a different input
    with an identity of its own. An interval nobody confirmed still blocks. So
    does one whose batch nobody ever read, for as long as its work is alive:
    that is the interval a receipt is still on its way for. A batch nobody read
    whose work the platform has already ended cannot get one
    (:func:`unread_input_with_over_work`).
    """
    rows = await session.scalars(
        select(NativeInput).where(
            NativeInput.topic_id == topic_id,
            NativeInput.recipient_handle == recipient_handle,
            ~unread_input_with_over_work(),
        )
    )
    return any(row.completed_at is None and row.terminated_at is None for row in rows)
