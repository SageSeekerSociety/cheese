"""Schedule receipt-driven Ask corrections through the process-owned runner."""

import asyncio
import logging
import uuid

from sqlalchemy import or_, select

from app.domain.block.models import Block
from app.domain.delivery.ask_receipt_wait import ASK_RECEIPT_WAIT, _has_group_receipt
from app.domain.delivery.models import Delivery, NativeInput


def nudge_ask_receipts(chat, identity):
    """One owned scan, after either wait-intent or native-receipt commit."""
    from app.domain.agent.pending_messages import current_runner

    runner = current_runner()
    caller = asyncio.current_task()
    if (
        runner is None
        or not runner.owns_sessions
        or not runner.accepting_turns
        or (caller is not None and caller.cancelling())
    ):
        return

    async def scan():
        try:
            await wake_ask_receipts(chat, identity, runner=runner)
        except Exception:
            logging.getLogger(__name__).exception(
                "Ask receipt wake failed topic=%s work=%s",
                identity.conversation_id,
                identity.work_id,
            )

    task = asyncio.create_task(scan(), name=f"ask-receipt:{identity.conversation_id}")
    runner._tasks.add(task)
    task.add_done_callback(runner._tasks.discard)


async def wake_ask_receipts(chat, identity, *, runner):
    """Retry only marked, unregistered intent through the normal dispatcher.

    Delivery locks come first. All input/block/proof reads are unlocked; no new
    reverse lock edge and no DB transaction survives into receiver admission.
    A registered or uncertain outcome is never converted into another send.
    """
    ready = []
    async with chat.session_factory() as session:
        rows = list(
            await session.scalars(
                select(Delivery)
                .where(
                    Delivery.conversation_id == identity.conversation_id,
                    Delivery.recipient_handle == identity.recipient_handle,
                    Delivery.state == "pending",
                    Delivery.sent_at.is_(None),
                    Delivery.lease_until.is_(None),
                    Delivery.payload[ASK_RECEIPT_WAIT]["project_id"].as_string()
                    == str(identity.project_id),
                    Delivery.payload[ASK_RECEIPT_WAIT]["harness"].as_string()
                    == identity.harness,
                    Delivery.payload[ASK_RECEIPT_WAIT]["native_session_id"].as_string()
                    == identity.native_session_id,
                    Delivery.payload[ASK_RECEIPT_WAIT]["work_id"].as_string()
                    == str(identity.work_id),
                )
                .order_by(Delivery.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        if not rows:
            return 0
        inputs = list(
            await session.scalars(
                select(NativeInput)
                .where(
                    NativeInput.project_id == identity.project_id,
                    NativeInput.conversation_id == identity.conversation_id,
                    NativeInput.recipient_handle == identity.recipient_handle,
                )
                .execution_options(populate_existing=True)
            )
        )
        held_ids = {
            uuid.UUID(value)
            for row in inputs
            for value in set(row.held_block_ids) - set(row.released_block_ids)
        }
        wakes = list(
            await session.scalars(
                select(Block)
                .where(
                    Block.project_id == identity.project_id,
                    Block.conversation_id == identity.conversation_id,
                    or_(
                        Block.id.in_(held_ids),
                        Block.meta["delivery_event_id"]
                        .as_string()
                        .in_(str(row.event_id) for row in rows),
                    ),
                )
                .execution_options(populate_existing=True)
            )
        )
        events = {
            uuid.UUID(block.meta["delivery_event_id"])
            for block in wakes
            if (block.meta or {}).get("answer_group")
            and (block.meta or {}).get("delivery_event_id")
        }
        previous = list(
            await session.scalars(
                select(Delivery)
                .where(
                    Delivery.conversation_id == identity.conversation_id,
                    Delivery.recipient_handle == identity.recipient_handle,
                    Delivery.event_id.in_(events),
                )
                .execution_options(populate_existing=True)
            )
        )
        deliveries = {str(row.event_id): row for row in previous}
        for row in rows:
            wait = row.payload[ASK_RECEIPT_WAIT]
            origin = row.payload.get("ask_origin") or {}
            if (
                wait.get("attempt_id") != str(row.attempt_id)
                or wait.get("ask_group") != row.payload.get("ask_group")
                or any(
                    origin.get(field) != getattr(identity, field)
                    for field in ("recipient_handle", "harness", "native_session_id")
                )
            ):
                continue
            own_wakes = {
                str(block.id)
                for block in wakes
                if (block.meta or {}).get("delivery_event_id") == str(row.event_id)
            }
            if not own_wakes or any(
                prior.delivery_id == row.id
                or prior.event_id == row.event_id
                or own_wakes.intersection(
                    prior.held_block_ids + prior.block_ids + prior.seen_block_ids
                )
                for prior in inputs
            ):
                continue
            if not _has_group_receipt(identity, row, inputs, wakes, deliveries):
                continue
            row.retry_at = None
            row.payload = {
                key: value
                for key, value in row.payload.items()
                if key != ASK_RECEIPT_WAIT
            }
            ready.append(row.id)
        await session.commit()
    if ready:
        from app.domain.delivery.agent import dispatch_pending

        await dispatch_pending(
            chat.session_factory, chat=chat, runner=runner, delivery_ids=ready
        )
    return len(ready)
