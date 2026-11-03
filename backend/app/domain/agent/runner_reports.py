"""Apply what a seat's runner reported, once: the platform's half of the protocol.

A ``Report`` (`agent/reads.py`) is one event a runner wrote
(`harness/driven/lifecycle.py`). Applying it is one transaction: the event is
claimed (``session_state.take``, which fences the writer's epoch and moves the
seat's offset past it), and everything it changes in the database is written
with that claim: an input's delivery, the seat's worker status, a failure on
the inputs a failed work read, a task's session having left or come back.

What follows from it outside that transaction is its projection: what is told
(a frame, a nudge to the waiting messages), done here after the commit from
what the transaction recorded, and the room's books on the open work and the
closing result the room hears, done by the seat's reading
(`room/sessions/reading.py`). The event counts as followed through only once
all of that is done (``projected``), and it is acknowledged to the runner only
then. Delivered again before that, it changes nothing in the database a second
time, and its projection is done again: every part of it is idempotent.

This sits beside the room, not in it: it settles deliveries and resumes
(`turn_resume`), which reach the room.
"""

import uuid
from dataclasses import dataclass

from app.domain.agent import session_state
from app.domain.agent.harness.driven.lifecycle import IDLE, RUNNING
from app.domain.agent.pending_messages import nudge_messages, wake_held_deliveries
from app.domain.agent.platform_failures import RUNNER_REPLACED_CODE
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
from app.domain.agent.turn_resume import started_on_receipt, started_record
from app.domain.block.queries import reaction_summaries_for_blocks
from app.domain.delivery.input_identity import InputReceipt
from app.domain.delivery.receipts import (
    drop_input,
    drop_unread_of_session,
    fail_read_inputs,
    hand_to,
    read_inputs_of_session,
    receiver_exited,
    record_receipt,
)
from app.domain.room_task.live import sessions_moved
from app.domain.room_task.progress import note_agent_back, note_agent_left


def _key(report: Report) -> session_state.SeatKey:
    return session_state.SeatKey(
        report.conversation_id, report.agent_handle, report.harness
    )


@dataclass(frozen=True)
class Applied:
    """What follows from a report is to be done now (``projected`` once it is).
    ``result_landed``: the room already landed the closing result it carries,
    in an earlier delivery of it (``landed``)."""

    result_landed: bool = False


async def apply(chat, report: Report) -> Applied | bool:
    """Apply ``report`` for ``chat``'s rooms, and tell who has to know.
    ``Applied`` when what follows from it is to be done now: it is new, or it
    was applied and not followed through. False when both were done before:
    nothing is done again."""
    key = _key(report)
    fact = report.fact
    started = None
    reactions: dict = {}
    freed = False
    async with chat.session_factory() as db:
        claim = await session_state.take(
            db,
            key,
            worker_epoch=report.worker_epoch,
            epoch=report.epoch,
            seq=report.seq,
        )
        if claim is session_state.Claim.DONE:
            await db.commit()
            return False
        new = claim is session_state.Claim.NEW
        already_landed = not new and await session_state.result_landed(
            db, key, epoch=report.epoch, seq=report.seq
        )
        if isinstance(fact, InputWritten):
            if new:
                await record_receipt(db, InputReceipt(fact.identity, "accepted"))
                # The process that wrote it is the one that holds it now.
                await hand_to(
                    db,
                    harness=fact.identity.harness,
                    native_session_id=fact.identity.native_session_id,
                    input_id=fact.identity.input_id,
                    epoch=report.epoch,
                )
        elif isinstance(fact, InputRead):
            # Applied again, a read records nothing new (`record_receipt` is
            # idempotent) and hands back the row it settled, to tell from.
            row = await record_receipt(db, fact.receipt)
            if row is not None and row.settled_at is not None:
                reactions = await reaction_summaries_for_blocks(
                    db, [uuid.UUID(block) for block in row.seen_block_ids]
                )
                # #4389: a resume instruction the session read is a resume
                # that started.
                started = await (started_on_receipt if new else started_record)(
                    db, row.delivery_id, row.attempt_id
                )
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
                if new
                else True
            )
        elif not new:
            pass
        elif isinstance(fact, SessionRunning):
            await session_state.declare(db, key, RUNNING)
            # The run this event begins, named as the runner names it to a
            # platform call made in it, so a report carries the same name. Every
            # running event counts, whichever registration delivers it: events
            # apply in the order the runner wrote them, so the last one applied
            # is the run going on, and a backlog a backend re-attaching the
            # runner reads (written under the epoch before) is still that run.
            await session_state.run_started(db, key, epoch=report.epoch, seq=report.seq)
            if report.of_this_registration:
                # The seat's current session is at work: the task's teammate is
                # back, whatever an earlier session's exit said.
                await note_agent_back(
                    db, report.conversation_id, by=report.agent_handle
                )
            # A task's state reads whether its teammate's session runs and which
            # run it is in (`presentation.task_presentation`), so its pages read
            # it again.
            await sessions_moved(db, [report.conversation_id])
        elif isinstance(fact, SessionIdle):
            if await session_state.declare(db, key, IDLE):
                await sessions_moved(db, [report.conversation_id])
            if fact.failure is not None and fact.result.session_id:
                await fail_read_inputs(
                    db,
                    harness=report.harness,
                    native_session_id=fact.result.session_id,
                    input_ids=fact.read_inputs,
                    failure=fact.failure,
                )
        elif isinstance(fact, SessionExited):
            # Whatever process it was, what was last handed to it and is still
            # unread will never be read.
            await receiver_exited(
                db,
                conversation_id=report.conversation_id,
                recipient_handle=report.agent_handle,
                harness=report.harness,
                epoch=report.epoch,
            )
            # Only the exit of a process registered under the seat's current
            # epoch ends its session. An earlier process's exit, stamped with
            # the epoch it was registered under, leaves the session that
            # replaced it as it is.
            if await session_state.exited(db, key, report.at, epoch=report.epoch):
                await sessions_moved(db, [report.conversation_id])
                # A task's session ending is noted beside that teammate's last
                # report — only when the report is the one that ended.
                await note_agent_left(
                    db, report.conversation_id, by=report.agent_handle, at=report.at
                )
        await db.commit()
    if started is not None:
        payload, room = started
        await publish(payload, room=room)
    if isinstance(fact, InputRead | InputDropped):
        identity = (
            fact.receipt.identity if isinstance(fact, InputRead) else fact.identity
        )
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
    return Applied(result_landed=already_landed)


async def landed(chat, report: Report) -> None:
    """The room landed the closing result ``report`` carries
    (``session_state.landed``)."""
    await session_state.landed(
        chat.session_factory, _key(report), epoch=report.epoch, seq=report.seq
    )


async def projected(chat, report: Report) -> None:
    """Everything that follows from ``report`` was done: delivered again, it
    asks nothing more (``session_state.projected``)."""
    await session_state.projected(
        chat.session_factory, _key(report), epoch=report.epoch, seq=report.seq
    )


async def replaced(
    chat,
    conversation_id: uuid.UUID,
    agent_handle: str,
    harness: str,
    native: str,
    *,
    read: tuple[tuple[str, tuple[str, ...]], ...] = (),
) -> None:
    """The process holding native session ``native`` was replaced by one of
    this release's: what it was written and never read is dropped as its
    runner would have dropped it at its exit, and the seat may take what was
    waiting for it. The inputs its runner reported its works read (``read``)
    are recorded as read, each into the work that read it; only the rest is
    dropped."""
    async with chat.session_factory() as db:
        for work, input_ids in read:
            await read_inputs_of_session(
                db,
                harness=harness,
                native_session_id=native,
                input_ids=input_ids,
                work_id=uuid.UUID(work),
            )
        await drop_unread_of_session(
            db, harness=harness, native_session_id=native, reason=RUNNER_REPLACED_CODE
        )
        await db.commit()
    nudge_messages(chat, conversation_id)
    wake_held_deliveries(chat, conversation_id, agent_handle)
