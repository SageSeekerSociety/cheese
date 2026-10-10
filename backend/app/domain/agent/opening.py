"""Where a new task's first instruction is: drafting its document from where
the task came from.

Nothing new is stored for it. The instruction is a delivery like any other,
marked with what it is for (``purpose``), and the turn it started is an agent
turn like any other; this reads the two and says what someone opening the task
should be told while its document is still empty.
"""

from __future__ import annotations

import uuid
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.agent.models import AgentTurn
from app.domain.block.models import Block, BlockKind
from app.domain.delivery.models import Delivery
from app.domain.identity.handles import agent_handle_column

#: The payload key a task instruction carries what it is for under.
PURPOSE = "purpose"
#: The first instruction a task created from a discussion gets.
OPENING = "opening"

OpeningState = Literal["drafting", "waiting", "failed"]


async def opening_state(
    session: AsyncSession, task_id: uuid.UUID
) -> OpeningState | None:
    """``drafting`` while the first turn is about to start or running,
    ``waiting`` when it could not start yet and will try again, ``failed`` when
    it gave up or ended having said and written nothing. None when there was no
    such instruction, or it ended and the teammate said something: then the
    conversation says what happened. The caller asks only while the task's
    document is empty."""
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
    # ``uncertain`` alone is not a failure: the send returns before the session's
    # receipt is recorded, so for the first seconds of a normal turn the row
    # reads ``uncertain``. The turn it started says whether it is still going.
    if row.state not in ("received", "uncertain"):
        return "waiting" if row.attempts > 0 and row.retry_at else "drafting"
    turn = await session.get(AgentTurn, row.attempt_id) if row.attempt_id else None
    if turn is None:
        return "failed" if row.state == "uncertain" else "drafting"
    if turn.stopped_at is None:
        return "drafting"
    spoke = await session.scalar(
        select(Block.id)
        .where(
            Block.conversation_id == task_id,
            Block.kind == BlockKind.message,
            agent_handle_column(Block.author),
            Block.created_at >= turn.started_at,
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
