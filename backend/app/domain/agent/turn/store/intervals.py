"""Committed execution intervals, without scheduling or publication.

The runner owns when an interval opens or closes. This module owns the exact
repository write and its commit, returning only after durability is established.
"""

import uuid
from collections.abc import Iterable
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.agent.repositories import AgentTurnRepository


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
