"""Committed execution intervals, without scheduling or publication.

The runner owns when an interval opens or closes. This module owns the exact
repository write and its commit, returning only after durability is established.
"""

import logging
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.agent.repositories import AgentTurnRepository

logger = logging.getLogger(__name__)


async def note_turn_context(
    sessions: async_sessionmaker[AsyncSession],
    turn_id: uuid.UUID,
    *,
    route: str,
    reply_to: uuid.UUID | None,
    agent_handle: str,
) -> None:
    """Best-effort, like the delivery stamp: losing it costs a turn picked
    up by another backend its reply link and the accuracy of one route
    label. It lands after the interval exists, so the row carries this
    turn's exact seat and route — what death evidence is matched against,
    never a room-level guess."""
    try:
        async with sessions() as session:
            await AgentTurnRepository(session).note_context(
                turn_id, route=route, reply_to=reply_to, agent_handle=agent_handle
            )
            await session.commit()
    except Exception:  # noqa: BLE001 — bookkeeping must not stop a turn
        logger.exception("could not record the context of turn %s", turn_id)


async def open_turn(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    turn_id: uuid.UUID,
    conversation_id: uuid.UUID,
    continuation_id: uuid.UUID,
    author: str,
    content: str,
    is_resume: bool,
    resendable: bool,
    started_at: datetime,
    delivered_at: datetime | None = None,
    agent_handle: str | None = None,
    session_id: str | None = None,
    exists_ok: bool = False,
) -> None:
    async with session_factory() as session:
        await AgentTurnRepository(session).open(
            turn_id=turn_id,
            conversation_id=conversation_id,
            continuation_id=continuation_id,
            author=author,
            content=content,
            is_resume=is_resume,
            resendable=resendable,
            started_at=started_at,
            delivered_at=delivered_at,
            agent_handle=agent_handle,
            session_id=session_id,
            exists_ok=exists_ok,
        )
        await session.commit()


async def close_turns(
    session_factory: async_sessionmaker[AsyncSession], turn_ids: Iterable[uuid.UUID]
) -> None:
    async with session_factory() as session:
        await AgentTurnRepository(session).close(turn_ids, datetime.now(UTC))
        await session.commit()


class InsideInputs(Protocol):
    async def __call__(
        self, session: AsyncSession, topic_id: uuid.UUID, work_id: uuid.UUID
    ) -> list[uuid.UUID]: ...


async def answered_open_turns(
    sessions: async_sessionmaker[AsyncSession],
    inside_inputs: InsideInputs,
    topic_id: uuid.UUID,
    turn_id: uuid.UUID,
) -> tuple[list[uuid.UUID], set[uuid.UUID]]:
    """Read the actual receipt-selected works and open intervals in one session."""
    async with sessions() as session:
        read_inside = await inside_inputs(session, topic_id, turn_id)
        open_turns = set(
            await AgentTurnRepository(session).still_open(topic_id, read_inside)
        )
    return read_inside, open_turns


async def turn_credits_refused(
    sessions: async_sessionmaker[AsyncSession], turn_id: uuid.UUID
) -> bool:
    async with sessions() as session:
        return await AgentTurnRepository(session).credits_refused(turn_id)
