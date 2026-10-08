"""Reading and writing where an agent's session in a conversation got to.

Thin over the repository on purpose — there is no policy here, only the one
question every caller asks in the same words: given a conversation (a room or a
task, by its id) and an agent, what does its session resume by. It exists as a
service because the callers are in other domains (the turn path in ``agent``,
clone in ``topic``), and a domain reaching into another's repository is what
the import guard forbids.
"""

import uuid
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.agent_session.models import LOST_KEY, AgentSession, SessionPlace
from app.domain.agent_session.repositories import AgentSessionRepository


class AgentSessionService:
    def __init__(self, session: AsyncSession):
        self._repo = AgentSessionRepository(session)

    async def ensure(
        self, conversation_id: uuid.UUID, agent_handle: str, *, harness: str
    ) -> AgentSession:
        """Create the conversation identity before any machine is acquired."""
        return await self._repo.ensure(conversation_id, agent_handle, harness)

    async def by_id(
        self, session_id: uuid.UUID, *, lock: bool = False
    ) -> AgentSession | None:
        return await self._repo.by_id(session_id, lock=lock)

    async def resume_token(
        self, conversation_id: uuid.UUID, agent_handle: str, *, harness: str
    ) -> str | None:
        """What this agent resumes its session in this conversation by."""
        return await self._repo.resume_token(conversation_id, agent_handle, harness)

    async def remember(
        self,
        *,
        conversation_id: uuid.UUID,
        agent_handle: str,
        resume_token: str,
        harness: str,
    ) -> None:
        """Record where this agent's conversation got to."""
        await self._repo.save(
            conversation_id=conversation_id,
            agent_handle=agent_handle,
            resume_token=resume_token,
            harness=harness,
        )

    async def told(
        self, conversation_id: uuid.UUID, agent_handle: str, *, harness: str
    ) -> dict | None:
        """The project state this agent's conversation was last told, by section."""
        row = await self._repo.get(conversation_id, agent_handle, harness)
        return row.told if row is not None else None

    async def remember_told(
        self, *, conversation_id: uuid.UUID, agent_handle: str, harness: str, told: dict
    ) -> None:
        """Record the project state this agent's conversation now knows."""
        await self._repo.save_told(
            conversation_id=conversation_id,
            agent_handle=agent_handle,
            harness=harness,
            told=told,
        )

    async def place(
        self, conversation_id: uuid.UUID, agent_handle: str, *, harness: str
    ) -> SessionPlace | None:
        """Where this agent's conversation here is — its machines, resolved."""
        row = await self._repo.get(conversation_id, agent_handle, harness)
        return row.place() if row is not None else None

    async def ids_on_choice(
        self, room_id: uuid.UUID, task_id: uuid.UUID | None = None
    ) -> list[uuid.UUID]:
        """The sessions that move when this room's (or this task's) work
        computer changes, in a stable order."""
        return await self._repo.ids_on_choice(room_id, task_id)

    async def remember_place(
        self,
        *,
        conversation_id: uuid.UUID,
        agent_handle: str,
        harness: str,
        work_lease: dict | None,
        runtime_location: dict,
    ) -> None:
        """Record the machines this session took."""
        await self._repo.save_place(
            conversation_id=conversation_id,
            agent_handle=agent_handle,
            harness=harness,
            work_lease=work_lease,
            runtime_location=runtime_location,
        )

    async def places_in_room(self, room_id: uuid.UUID) -> list[SessionPlace]:
        """Every place a session in this room is sitting on."""
        rows = await self._repo.placed_in_room(room_id)
        return [place for row in rows if (place := row.place()) is not None]

    async def sandbox_lost_in_room(self, room_id: uuid.UUID) -> bool:
        """A session in this room had its cloud sandbox destroyed and has not
        been placed in a new one yet (``LOST_KEY``)."""
        rows = await self._repo.placed_in_room(room_id)
        return any((row.execution_request or {}).get(LOST_KEY) for row in rows)

    async def harness_in_room(self, room_id: uuid.UUID) -> str | None:
        """Which harness the room's one pane belongs to, if anything is on it.

        One name and not a set: a room has a single screen, and it shows the
        program of whichever session last opened one. A room that seats a
        claude-code teammate and is now running pi has two session rows and one
        black pane — answering "some session here draws" would put that pane in
        front of a person and take the 施工记录 timeline away to do it.
        """
        placed = await self._repo.placed_in_room(room_id)
        return placed[0].harness if placed else None

    async def placed_sessions(
        self,
    ) -> list[
        tuple[
            uuid.UUID,
            uuid.UUID,
            uuid.UUID | None,
            str,
            str,
            str | None,
            SessionPlace,
            bool,
        ]
    ]:
        """``(project_id, room_id, inner_id, agent_handle, harness, resume_token,
        place, let_go)`` for every placed session — what a channel re-adopts after a
        restart. ``inner_id`` is the task's or the 支线's, None for a room's
        own session.

        One per (conversation, agent, harness) seat, and each comes back on its
        own seat. Every channel reads this same list and keeps the rows whose
        harness is its own. The stored resume token travels because it is the
        provenance a terminal answer has to match before it may close anything
        (FB-56).
        """
        found = []
        for row, project_id, room_id in await self._repo.placed_everywhere():
            place = row.place()
            if place is not None:
                found.append(
                    (
                        project_id,
                        room_id,
                        row.conversation_id if row.conversation_id != room_id else None,
                        row.agent_handle,
                        row.harness,
                        row.resume_token,
                        place,
                        bool((row.runtime_location or {}).get("let_go")),
                    )
                )
        return found

    async def let_go(
        self,
        *,
        conversation_id: uuid.UUID,
        agent_handle: str,
        harness: str,
        placed_before: datetime,
    ) -> None:
        """Record that this session's runner is gone; see the repository."""
        await self._repo.let_go(
            conversation_id=conversation_id,
            agent_handle=agent_handle,
            harness=harness,
            placed_before=placed_before,
        )

    async def has_run(self, room_id: uuid.UUID) -> bool:
        """Whether ANY agent has ever run here — what the compute pin freezes on."""
        return await self._repo.has_any(room_id)

    async def forget_room(self, room_id: uuid.UUID) -> None:
        await self._repo.forget_room(room_id)
