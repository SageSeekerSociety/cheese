"""Reading and writing where an agent's conversation in a place got to.

Thin over the repository on purpose — there is no policy here, only the one
question every caller asks in the same words: given a place and an agent, what
does its conversation resume by. It exists as a service because the callers are
in other domains (the turn path in ``agent``, clone in ``topic``), and a domain
reaching into another's repository is what the import guard forbids.
"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.agent.harness import DEFAULT_HARNESS
from app.domain.agent_session.models import SessionPlace
from app.domain.agent_session.repositories import AgentSessionRepository


class AgentSessionService:
    def __init__(self, session: AsyncSession):
        self._repo = AgentSessionRepository(session)

    async def resume_token(
        self, topic_id: uuid.UUID, agent_handle: str, *, harness: str = DEFAULT_HARNESS
    ) -> str | None:
        """What this agent resumes its conversation in this topic by."""
        return await self._repo.resume_token(topic_id, agent_handle, harness)

    async def remember(
        self,
        *,
        topic_id: uuid.UUID,
        agent_handle: str,
        resume_token: str,
        harness: str = DEFAULT_HARNESS,
    ) -> None:
        """Record where this agent's conversation got to."""
        await self._repo.save(
            topic_id=topic_id,
            agent_handle=agent_handle,
            resume_token=resume_token,
            harness=harness,
        )

    async def place(
        self, topic_id: uuid.UUID, agent_handle: str, *, harness: str = DEFAULT_HARNESS
    ) -> SessionPlace | None:
        """Where this agent's conversation here is — its machines, resolved."""
        row = await self._repo.get(topic_id, agent_handle, harness)
        return row.place() if row is not None else None

    async def remember_place(
        self,
        *,
        topic_id: uuid.UUID,
        agent_handle: str,
        harness: str = DEFAULT_HARNESS,
        work_lease: dict | None,
        runtime_location: dict,
    ) -> None:
        """Record the machines this session took."""
        await self._repo.save_place(
            topic_id=topic_id,
            agent_handle=agent_handle,
            harness=harness,
            work_lease=work_lease,
            runtime_location=runtime_location,
        )

    async def places_in_room(self, room_id: uuid.UUID) -> list[SessionPlace]:
        """Every place a session in this room is sitting on."""
        rows = await self._repo.placed_in_room(room_id)
        return [place for row in rows if (place := row.place()) is not None]

    async def harnesses_in_room(self, room_id: uuid.UUID) -> set[str]:
        """Which harnesses are running in this room right now."""
        return {row.harness for row in await self._repo.placed_in_room(room_id)}

    async def placed_sessions(
        self,
    ) -> list[tuple[uuid.UUID, uuid.UUID, str, str, SessionPlace]]:
        """``(project_id, room_id, agent_handle, harness, place)`` for every
        session on a machine — what a channel re-adopts after a restart."""
        found = []
        for row, project_id in await self._repo.placed_everywhere():
            place = row.place()
            if place is not None:
                found.append(
                    (project_id, row.topic_id, row.agent_handle, row.harness, place)
                )
        return found

    async def has_run(self, topic_id: uuid.UUID) -> bool:
        """Whether ANY agent has ever run here — what the compute pin freezes on."""
        return await self._repo.has_any(topic_id)

    async def forget_room(self, topic_id: uuid.UUID) -> None:
        await self._repo.forget_room(topic_id)
