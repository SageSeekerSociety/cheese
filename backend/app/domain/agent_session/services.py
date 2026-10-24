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
from app.domain.agent_session.repositories import (
    AgentReadRepository,
    AgentSessionRepository,
)


class AgentSessionService:
    def __init__(self, session: AsyncSession):
        self._repo = AgentSessionRepository(session)
        self._reads = AgentReadRepository(session)

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

    async def read_cursor(
        self, conversation_id: uuid.UUID, agent_handle: str
    ) -> tuple[datetime, uuid.UUID] | None:
        """Where this seat's reading of the conversation stands, or None."""
        return await self._reads.cursor(conversation_id, agent_handle)

    async def advance_read(
        self,
        *,
        conversation_id: uuid.UUID,
        agent_handle: str,
        at: datetime,
        block_id: uuid.UUID,
    ) -> None:
        """Move this seat's reading forward to ``(at, block_id)``."""
        await self._reads.advance(
            conversation_id=conversation_id,
            agent_handle=agent_handle,
            at=at,
            block_id=block_id,
        )

    async def remember_told(
        self,
        *,
        conversation_id: uuid.UUID,
        agent_handle: str,
        harness: str,
        told: dict,
        was: dict | None,
    ) -> None:
        """Record the project state this agent's conversation now knows, unless
        the record is no longer ``was``, the one this turn chose what to tell
        by (see the repository)."""
        await self._repo.save_told(
            conversation_id=conversation_id,
            agent_handle=agent_handle,
            harness=harness,
            told=told,
            was=was,
        )

    async def forget_told(
        self, *, conversation_id: uuid.UUID, agent_handle: str, harness: str
    ) -> None:
        """This agent's conversation was compacted: what it was told is now a
        summary at best, so the next input tells it the whole state again."""
        await self._repo.forget_told(
            conversation_id=conversation_id,
            agent_handle=agent_handle,
            harness=harness,
        )

    async def place(
        self, conversation_id: uuid.UUID, agent_handle: str, *, harness: str
    ) -> SessionPlace | None:
        """Where this agent's conversation here is — its machines, resolved."""
        row = await self._repo.get(conversation_id, agent_handle, harness)
        return row.place() if row is not None else None

    async def last_used_on(
        self, channel: str, machines: list[str]
    ) -> dict[str, datetime]:
        """When a session of ``channel`` last ran on each of ``machines``."""
        return await self._repo.last_used_on(channel, machines)

    async def devices_held_in_room(self, room_id: uuid.UUID) -> list[str]:
        """The people's machines the room's sessions (its tasks' included) hold
        a lease on now: a sandbox on one of the platform's machines is not one."""
        held: list[str] = []
        for row in await self._repo.placed_in_room(room_id):
            lease = row.work_lease or {}
            device_id = lease.get("device_id")
            if device_id and not lease.get("platform") and device_id not in held:
                held.append(device_id)
        return held

    async def latest(self, conversation_id: uuid.UUID, agent_handle: str):
        """This agent's most recent session row here, whatever its harness."""
        return await self._repo.latest(conversation_id, agent_handle)

    async def environment(
        self, conversation_id: uuid.UUID, agent_handle: str, *, lock: bool = False
    ):
        """This agent's execution environment here, as stored, or None."""
        return await self._repo.environment(conversation_id, agent_handle, lock=lock)

    async def environments_in(self, conversation_id: uuid.UUID):
        """Every agent's stored environment in this conversation, by handle."""
        return await self._repo.environments_in(conversation_id)

    async def set_environment(
        self,
        conversation_id: uuid.UUID,
        agent_handle: str,
        environment: dict,
        *,
        chosen_by: dict | None,
        only_if_unset: bool = False,
    ):
        return await self._repo.set_environment(
            conversation_id,
            agent_handle,
            environment,
            chosen_by=chosen_by,
            only_if_unset=only_if_unset,
        )

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

    async def working_copy(
        self, conversation_id: uuid.UUID, generation: str
    ) -> tuple[dict, bool] | None:
        """The machine lease holding this task's (or 支线's) working copy on the
        room's current ``generation``, and whether its cloud sandbox has been
        destroyed since (``LOST_KEY``).

        Each session works in a home of its own (``SandboxHome``), so a
        task's files are in its own session's home and in no other session's
        of the same room. None while no session of it has taken a machine:
        nothing holds a working copy of it yet.

        A lease that is not ready and names no executor has not taken its
        machine either: it is a reservation whose executor is still being
        installed there, or whose installation failed and waits for the next
        tool call to retry, and there is nothing on the machine to ask. One
        that is not ready but already records where its executor is (an
        installation that got that far) does hold the machine's copy, and that
        copy may carry work nobody has pushed, so it is still read."""
        for row in await self._repo.placed_in_conversation(conversation_id):
            place = row.place()
            if place is None or place.resource_id != generation:
                continue
            lease = place.lease
            if (
                lease is not None
                and lease.get("kind") == "device"
                and (
                    lease.get("status", "ready") == "ready"
                    or lease.get("state")
                    or lease.get("home")
                )
            ):
                return lease, bool((row.execution_request or {}).get(LOST_KEY))
        return None

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

    async def drain(
        self,
        *,
        conversation_id: uuid.UUID,
        agent_handle: str,
        harness: str,
        launch: str,
    ) -> datetime:
        """When the drain of this session's runner began; see the repository."""
        return await self._repo.drain(
            conversation_id=conversation_id,
            agent_handle=agent_handle,
            harness=harness,
            launch=launch,
        )

    async def drained(
        self, *, conversation_id: uuid.UUID, agent_handle: str, harness: str
    ) -> None:
        """The drain of this session's runner is over; see the repository."""
        await self._repo.drained(
            conversation_id=conversation_id, agent_handle=agent_handle, harness=harness
        )

    async def has_run(self, room_id: uuid.UUID) -> bool:
        """Whether ANY agent has ever run here — what the compute pin freezes on."""
        return await self._repo.has_any(room_id)

    async def forget_room(self, room_id: uuid.UUID) -> None:
        await self._repo.forget_room(room_id)
