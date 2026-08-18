"""Agent session data access."""

import uuid

from sqlalchemy import exists, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.agent_session.models import AgentSession


class AgentSessionRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def resume_token(self, topic_id: uuid.UUID, agent_handle: str) -> str | None:
        """What this agent resumes its conversation in this topic by."""
        result = await self._session.execute(
            select(AgentSession.resume_token).where(
                AgentSession.topic_id == topic_id,
                AgentSession.agent_handle == agent_handle,
            )
        )
        return result.scalar_one_or_none()

    async def save(
        self, *, topic_id: uuid.UUID, agent_handle: str, resume_token: str
    ) -> None:
        """Record where this agent's conversation got to (upsert).

        ON CONFLICT rather than get-then-insert: two turns can reach here at once
        (a resumed turn and the sweep's own resume are the pair that actually
        does), and losing that race must overwrite, not raise.
        """
        stmt = insert(AgentSession).values(
            topic_id=topic_id, agent_handle=agent_handle, resume_token=resume_token
        )
        await self._session.execute(
            stmt.on_conflict_do_update(
                constraint="uq_agent_session_topic",
                set_={"resume_token": stmt.excluded.resume_token},
            )
        )
        await self._session.flush()

    async def has_any(self, topic_id: uuid.UUID) -> bool:
        """Whether ANY agent has ever run here — i.e. whether the topic has run.

        What froze on ``topics.session_id IS NOT NULL`` (the compute pin, the
        workspace's ``has_run``) freezes on this: the first turn is still the
        first turn, whoever took it.
        """
        result = await self._session.execute(
            select(exists().where(AgentSession.topic_id == topic_id))
        )
        return bool(result.scalar())
