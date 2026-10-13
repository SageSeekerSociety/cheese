"""Commit the session-opening facts after transport acceptance."""

import uuid
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.agent_session.services import AgentSessionService


class DeliveryStamp(Protocol):
    async def __call__(
        self, db: AsyncSession, *, turn_id: uuid.UUID, at: datetime
    ) -> None: ...


async def remember_opening(
    sessions: async_sessionmaker[AsyncSession],
    *,
    topic_id: uuid.UUID,
    agent_handle: str,
    harness: str,
    digests: dict[str, str],
) -> None:
    async with sessions() as session:
        await AgentSessionService(session).remember_told(
            conversation_id=topic_id,
            agent_handle=agent_handle,
            harness=harness,
            told=digests,
        )
        await session.commit()


async def commit_delivery(
    sessions: async_sessionmaker[AsyncSession],
    *,
    stamp: DeliveryStamp,
    turn_id: uuid.UUID,
) -> None:
    async with sessions() as session:
        await stamp(session, turn_id=turn_id, at=datetime.now(UTC))
        await session.commit()
