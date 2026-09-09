"""Reading and writing where an agent's conversation in a place got to.

Thin over the repository on purpose — there is no policy here, only the one
question every caller asks in the same words: given a place and an agent, what
does its conversation resume by. It exists as a service because the callers are
in other domains (the turn path in ``agent``, clone in ``topic``), and a domain
reaching into another's repository is what the import guard forbids.
"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.agent_session.repositories import AgentSessionRepository


class AgentSessionService:
    def __init__(self, session: AsyncSession):
        self._repo = AgentSessionRepository(session)

    async def resume_token(self, topic_id: uuid.UUID, agent_handle: str) -> str | None:
        """What this agent resumes its conversation in this topic by."""
        return await self._repo.resume_token(topic_id, agent_handle)

    async def remember(
        self, *, topic_id: uuid.UUID, agent_handle: str, resume_token: str
    ) -> None:
        """Record where this agent's conversation got to."""
        await self._repo.save(
            topic_id=topic_id, agent_handle=agent_handle, resume_token=resume_token
        )

    async def has_run(self, topic_id: uuid.UUID) -> bool:
        """Whether ANY agent has ever run here — what the compute pin freezes on."""
        return await self._repo.has_any(topic_id)

    async def forget_room(self, topic_id: uuid.UUID) -> None:
        await self._repo.forget_room(topic_id)
