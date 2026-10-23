"""Where a new task's first instruction is: the work that sets out from where
the task came from, writing the task's document as it goes.

Nothing new is stored for it. The instruction is a delivery like any other,
marked with what it is for (``purpose``); the task has started once a session
read it. This reads the instruction, what became of the input that carried it,
and what the session that read it declares, and says what someone opening the
task should be told while its document is still empty.

Pages showing the task hear when the instruction moves on its ledger — waits to
try again, gives up — through the task's own live path (`room_task.live`), so
an open overview does not keep saying 正在做 after it failed.
"""

from __future__ import annotations

import uuid
from typing import Literal

from sqlalchemy import event, inspect, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.domain.agent.harness.driven.lifecycle import IDLE
from app.domain.agent.models import SessionState
from app.domain.block.models import Block, BlockKind
from app.domain.delivery.models import Delivery, NativeInput
from app.domain.identity.handles import agent_handle_column
from app.domain.room_task.live import moved_in_flush

#: The payload key a task instruction carries what it is for under.
PURPOSE = "purpose"
#: The first instruction a task created from a discussion gets.
OPENING = "opening"

OpeningState = Literal["drafting", "waiting", "failed"]

#: What :func:`opening_state` reads off the instruction's ledger row.
_READS = ("state", "retry_at", "attempt_id")


def _moved(session: Session, row: object) -> bool:
    """A task's first instruction was written, or moved on its ledger."""
    if not isinstance(row, Delivery) or (row.payload or {}).get(PURPOSE) != OPENING:
        return False
    if row in session.new:
        return True
    attrs = inspect(row).attrs
    return any(attrs[key].history.has_changes() for key in _READS)


@event.listens_for(Session, "after_flush")
def _note_openings(session: Session, _context) -> None:
    # `new` / `dirty` still describe what this flush wrote while it runs. A bulk
    # `update(Delivery)` is not seen here; those move a turn or the task's row
    # alongside, which tell the pages themselves.
    moved_in_flush(
        session,
        {
            row.conversation_id
            for row in (*session.new, *session.dirty)
            if _moved(session, row)
        },
    )


async def opening_state(
    session: AsyncSession, task_id: uuid.UUID
) -> OpeningState | None:
    """``drafting`` while the instruction is about to be read or its session is
    at work on it, ``waiting`` when it could not be handed over yet and will be
    tried again, ``failed`` when it was given up on, or read and then not
    acted on: the work it was read into failed, or its session stopped with
    nothing said since. None when there was no such instruction, or its session
    stopped having said something: then the conversation says what happened.
    The caller asks only while the task's document is empty.

    Every answer rests on a fact the platform can check: the instruction's
    ledger row, the session's echo of it (`NativeInput.echoed_at`, which settles
    the row as ``received``), the failure the runner reported on what that work
    read (`NativeInput.failed_at`), and the session's declared state
    (`SessionState`)."""
    row = await session.scalar(
        select(Delivery)
        .where(
            Delivery.conversation_id == task_id,
            Delivery.payload[PURPOSE].astext == OPENING,
        )
        .order_by(Delivery.recorded_at.desc())
        .limit(1)
    )
    if row is None:
        return None
    if row.state == "failed":
        return "failed"
    # ``uncertain``: written to the session, not read yet — what is about to
    # happen to it is the session's next read, or its runner dropping it, which
    # puts the row back to be tried again.
    if row.state != "received":
        if row.state == "uncertain":
            return "drafting"
        return "waiting" if row.attempts > 0 and row.retry_at else "drafting"
    read = await session.scalar(
        select(NativeInput)
        .where(
            NativeInput.delivery_id == row.id,
            NativeInput.attempt_id == row.attempt_id,
            NativeInput.echoed_at.is_not(None),
        )
        .limit(1)
    )
    if read is None:
        return "drafting"
    if read.failed_at is not None:
        return "failed"
    # The task's sessions, as their runners declare them: one still at work
    # is working on it; one that stopped after the read has done what it was
    # going to do with it.
    states = (
        await session.execute(
            select(SessionState.worker_status, SessionState.changed_at).where(
                SessionState.conversation_id == task_id
            )
        )
    ).all()
    if any(status != IDLE for status, _ in states):
        return "drafting"
    if not any(changed_at > read.echoed_at for _, changed_at in states):
        return "drafting"
    spoke = await session.scalar(
        select(Block.id)
        .where(
            Block.conversation_id == task_id,
            Block.kind == BlockKind.message,
            agent_handle_column(Block.author),
            Block.created_at >= read.echoed_at,
        )
        .limit(1)
    )
    return None if spoke is not None else "failed"


async def opening_content(session: AsyncSession, task_id: uuid.UUID) -> str | None:
    """What the task's first instruction said, to give it again."""
    row = await session.scalar(
        select(Delivery)
        .where(
            Delivery.conversation_id == task_id,
            Delivery.payload[PURPOSE].astext == OPENING,
        )
        .order_by(Delivery.recorded_at.desc())
        .limit(1)
    )
    return (row.payload or {}).get("content") if row is not None else None
