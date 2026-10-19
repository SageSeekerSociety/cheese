"""Apply what a seat's runner reported, once: the platform's half of the protocol.

A ``Report`` (`agent/reads.py`) is one event a runner wrote
(`harness/driven/lifecycle.py`). Applying it is one transaction: the event is
claimed (``session_state.take``, which fences the writer's epoch and moves the
seat's offset past it), and everything it changes in the database is written
with that claim — an input's delivery, the seat's worker status, a failure on
the inputs a failed work read, a task's session having left. A redelivered event claims nothing and changes
nothing. What only tells somebody — a frame, a nudge to the waiting messages —
follows the commit.

What the room does with a report beyond the database — the room's books on the
open work, the closing result the room hears — is the seat's reading's
(`room/sessions/reading.py`), after this. This sits beside the room, not in it:
it settles deliveries and resumes (`turn_resume`), which reach the room.
"""

import uuid

from app.domain.agent import session_state
from app.domain.agent.harness.driven.lifecycle import IDLE, RUNNING
from app.domain.agent.pending_messages import nudge_messages, wake_held_deliveries
from app.domain.agent.reads import (
    InputDropped,
    InputRead,
    InputWritten,
    Report,
    SessionExited,
    SessionIdle,
    SessionRunning,
)
from app.domain.agent.realtime.broker import get_broker
from app.domain.agent.run_records import publish
from app.domain.agent.turn_resume import started_on_receipt
from app.domain.block.queries import reaction_summaries_for_blocks
from app.domain.delivery.input_identity import InputReceipt
from app.domain.delivery.receipts import drop_input, fail_read_inputs, record_receipt
from app.domain.room_task.progress import note_agent_left


async def apply(chat, report: Report) -> bool:
    """Apply ``report`` for ``chat``'s rooms, and tell who has to know. False
    when it had been applied before: nothing is done again."""
    key = session_state.SeatKey(
        report.conversation_id, report.agent_handle, report.harness
    )
    fact = report.fact
    started = None
    reactions: dict = {}
    freed = False
    async with chat.session_factory() as db:
        if not await session_state.take(
            db,
            key,
            worker_epoch=report.worker_epoch,
            epoch=report.epoch,
            seq=report.seq,
        ):
            await db.commit()
            return False
        if isinstance(fact, InputWritten):
            await record_receipt(db, InputReceipt(fact.identity, "accepted"))
        elif isinstance(fact, InputRead):
            row = await record_receipt(db, fact.receipt)
            if row is not None and row.settled_at is not None:
                reactions = await reaction_summaries_for_blocks(
                    db, [uuid.UUID(block) for block in row.seen_block_ids]
                )
                # #4389: a resume instruction the session read is a resume
                # that started.
                started = await started_on_receipt(db, row.delivery_id, row.attempt_id)
        elif isinstance(fact, InputDropped):
            freed = (
                await drop_input(
                    db,
                    harness=fact.identity.harness,
                    native_session_id=fact.identity.native_session_id,
                    input_id=fact.identity.input_id,
                    reason=fact.reason,
                )
                is not None
            )
        elif isinstance(fact, SessionRunning):
            await session_state.declare(db, key, RUNNING, report.at)
        elif isinstance(fact, SessionIdle):
            await session_state.declare(db, key, IDLE, report.at)
            if fact.failure is not None and fact.result.session_id:
                await fail_read_inputs(
                    db,
                    harness=report.harness,
                    native_session_id=fact.result.session_id,
                    input_ids=fact.read_inputs,
                    failure=fact.failure,
                )
        elif isinstance(fact, SessionExited):
            await session_state.declare(db, key, IDLE, report.at, exited=True)
            # A task's session ending is noted beside its teammate's last report.
            await note_agent_left(db, report.conversation_id, at=report.at)
        await db.commit()
    if started is not None:
        payload, room = started
        await publish(payload, room=room)
    if isinstance(fact, InputRead | InputDropped):
        identity = fact.receipt.identity if isinstance(fact, InputRead) else fact.identity
        pending = chat.live.unread_inputs.get(identity.conversation_id)
        if pending is not None:
            pending.pop(identity.input_id, None)
    for block_id, value in reactions.items():
        await get_broker().publish(
            str(report.conversation_id),
            {"type": "reaction", "block_id": str(block_id), "reactions": value},
        )
    if freed or isinstance(fact, SessionIdle | SessionExited):
        # The seat may take what was waiting for it.
        nudge_messages(chat, report.conversation_id)
        wake_held_deliveries(chat, report.conversation_id, report.agent_handle)
    return True
