"""Agent session data access."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    and_,
    case,
    cast,
    delete,
    exists,
    func,
    literal,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.agent_session.models import AgentEnvironment, AgentRead, AgentSession
from app.domain.conversation.services import of_room, room_column
from app.domain.topic.models import Topic


class AgentSessionRepository:
    """Keyed by the conversation — a room or a task — a session is in.

    A room-wide question about sessions (every session of a room and its
    tasks) is asked of the conversation column through ``of_room``.
    """

    def __init__(self, session: AsyncSession):
        self._session = session

    async def ensure(
        self, conversation_id: uuid.UUID, agent_handle: str, harness: str
    ) -> AgentSession:
        await self._upsert(
            conversation_id=conversation_id,
            agent_handle=agent_handle,
            harness=harness,
            values={},
        )
        row = await self.get(conversation_id, agent_handle, harness)
        assert row is not None
        return row

    async def by_id(self, session_id: uuid.UUID, *, lock: bool = False):
        query = select(AgentSession).where(AgentSession.id == session_id)
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        return await self._session.scalar(query)

    async def resume_token(
        self, conversation_id: uuid.UUID, agent_handle: str, harness: str
    ) -> str | None:
        """What this agent resumes its conversation in this place by."""
        result = await self._session.execute(
            select(AgentSession.resume_token).where(
                AgentSession.conversation_id == conversation_id,
                AgentSession.agent_handle == agent_handle,
                AgentSession.harness == harness,
            )
        )
        return result.scalar_one_or_none()

    async def save(
        self,
        *,
        conversation_id: uuid.UUID,
        agent_handle: str,
        resume_token: str,
        harness: str,
    ) -> None:
        """Record where this agent's conversation got to (upsert).

        ON CONFLICT rather than get-then-insert: two turns can reach here at once
        (a resumed turn and the sweep's own resume are the pair that actually
        does), and losing that race must overwrite, not raise.
        """
        await self._upsert(
            conversation_id=conversation_id,
            agent_handle=agent_handle,
            harness=harness,
            values={"resume_token": resume_token},
        )

    async def save_told(
        self,
        *,
        conversation_id: uuid.UUID,
        agent_handle: str,
        harness: str,
        told: dict,
        was: dict | None,
    ) -> None:
        """Record what project state this agent's conversation has been told,
        unless the record moved off ``was`` (what the turn read before choosing
        what to tell) in the meantime.

        It moves when a compaction ends between that read and this write
        (``forget_told``): the turn told only what changed since ``was``, and
        writing its digests over the cleared record would say the unchanged
        sections are still in the window when the compaction just took them
        out. Left cleared, the next input tells the whole state.
        """
        now = datetime.now(UTC)
        stmt = insert(AgentSession).values(
            conversation_id=conversation_id,
            agent_handle=agent_handle,
            harness=harness,
            told=told,
            updated_at=now,
        )
        # `told` is a JSON column, which has no equality operator; JSONB does.
        unchanged = (
            AgentSession.told.is_(None)
            if was is None
            else cast(AgentSession.told, JSONB) == cast(literal(was, JSON), JSONB)
        )
        await self._session.execute(
            stmt.on_conflict_do_update(
                index_elements=[
                    AgentSession.conversation_id,
                    AgentSession.agent_handle,
                    AgentSession.harness,
                ],
                set_={"told": stmt.excluded.told, "updated_at": now},
                where=unchanged,
            )
        )
        await self._session.flush()

    async def forget_told(
        self, *, conversation_id: uuid.UUID, agent_handle: str, harness: str
    ) -> None:
        """Record that this conversation no longer holds what it was told."""
        await self._session.execute(
            update(AgentSession)
            .where(
                AgentSession.conversation_id == conversation_id,
                AgentSession.agent_handle == agent_handle,
                AgentSession.harness == harness,
            )
            .values(told=None, updated_at=datetime.now(UTC))
        )

    async def save_place(
        self,
        *,
        conversation_id: uuid.UUID,
        agent_handle: str,
        harness: str,
        work_lease: dict | None,
        runtime_location: dict,
    ) -> None:
        """Record where this session rented hands and where its process runs.

        The same upsert as ``save`` and for the same reason: setup can reach
        here while a sweep is writing the resume token, and each write must
        leave the other's column alone rather than raise or clobber it.

        ``placed_at`` is stamped from here and from nowhere else — it is the
        moment this session took a machine, which is what orders the sessions
        of one room (``placed_in_room``). Stamping it in ``_upsert`` would make
        it「最后写过任何一列」, and every turn's resume token writes a column.
        """
        await self._upsert(
            conversation_id=conversation_id,
            agent_handle=agent_handle,
            harness=harness,
            values={
                "work_lease": work_lease,
                "runtime_location": runtime_location,
                "placed_at": datetime.now(UTC),
            },
        )

    async def let_go(
        self,
        *,
        conversation_id: uuid.UUID,
        agent_handle: str,
        harness: str,
        placed_before: datetime,
    ) -> None:
        """Mark this placement's runner as gone (``RoomSessions.recover``).

        The place itself stays: the next message starts the session again on
        it, and that start writes a fresh ``runtime_location`` without the
        mark. Only a placement made before ``placed_before`` is marked, so a
        session started while recovery was asking keeps its new place.
        """
        row = await self._session.scalar(
            select(AgentSession)
            .where(
                AgentSession.conversation_id == conversation_id,
                AgentSession.agent_handle == agent_handle,
                AgentSession.harness == harness,
            )
            .with_for_update()
        )
        if row is None or not row.runtime_location:
            return
        if row.placed_at is not None and row.placed_at > placed_before:
            return
        row.runtime_location = {**row.runtime_location, "let_go": True}
        await self._session.flush()

    async def drain(
        self,
        *,
        conversation_id: uuid.UUID,
        agent_handle: str,
        harness: str,
        launch: str,
    ) -> datetime:
        """When the drain of runner ``launch`` on this session began: now,
        unless a drain of that same runner is already under way
        (``AgentSession.drain_started_at``)."""
        same = and_(
            AgentSession.drain_launch == launch,
            AgentSession.drain_started_at.is_not(None),
        )
        started = await self._session.scalar(
            update(AgentSession)
            .where(
                AgentSession.conversation_id == conversation_id,
                AgentSession.agent_handle == agent_handle,
                AgentSession.harness == harness,
            )
            .values(
                drain_started_at=case(
                    (same, AgentSession.drain_started_at), else_=func.now()
                ),
                drain_launch=launch,
            )
            .returning(AgentSession.drain_started_at)
        )
        # A seat recovery found has its row; one deleted since starts now.
        return started or datetime.now(UTC)

    async def drained(
        self, *, conversation_id: uuid.UUID, agent_handle: str, harness: str
    ) -> None:
        """The drain of this session's runner is over: it was replaced."""
        await self._session.execute(
            update(AgentSession)
            .where(
                AgentSession.conversation_id == conversation_id,
                AgentSession.agent_handle == agent_handle,
                AgentSession.harness == harness,
            )
            .values(drain_started_at=None, drain_launch=None)
        )

    async def _upsert(
        self,
        *,
        conversation_id: uuid.UUID,
        agent_handle: str,
        harness: str,
        values: dict,
    ) -> None:
        # `updated_at` is stamped here by hand: ON CONFLICT DO UPDATE writes
        # exactly the columns named in `set_`, so the mapper's `onupdate` never
        # fires and the row would go on saying it was last touched when it was
        # created. It says exactly that and nothing more — which session opened
        # the room's pane is `placed_at`, written only by `save_place`.
        values = {**values, "updated_at": datetime.now(UTC)}
        stmt = insert(AgentSession).values(
            conversation_id=conversation_id,
            agent_handle=agent_handle,
            harness=harness,
            **values,
        )
        await self._session.execute(
            stmt.on_conflict_do_update(
                index_elements=[
                    AgentSession.conversation_id,
                    AgentSession.agent_handle,
                    AgentSession.harness,
                ],
                set_={key: getattr(stmt.excluded, key) for key in values},
            )
        )
        await self._session.flush()

    async def get(
        self, conversation_id: uuid.UUID, agent_handle: str, harness: str
    ) -> AgentSession | None:
        """This agent's session row in this place, if it has one."""
        result = await self._session.execute(
            select(AgentSession).where(
                AgentSession.conversation_id == conversation_id,
                AgentSession.agent_handle == agent_handle,
                AgentSession.harness == harness,
            )
        )
        return result.scalar_one_or_none()

    async def latest(
        self, conversation_id: uuid.UUID, agent_handle: str
    ) -> AgentSession | None:
        return await self._session.scalar(
            select(AgentSession)
            .where(
                AgentSession.conversation_id == conversation_id,
                AgentSession.agent_handle == agent_handle,
            )
            .order_by(AgentSession.updated_at.desc())
            .limit(1)
        )

    async def environment(
        self, conversation_id: uuid.UUID, agent_handle: str, *, lock: bool = False
    ) -> AgentEnvironment | None:
        query = select(AgentEnvironment).where(
            AgentEnvironment.conversation_id == conversation_id,
            AgentEnvironment.agent_handle == agent_handle,
        )
        if lock:
            query = query.with_for_update()
        return (await self._session.execute(query)).scalar_one_or_none()

    async def environments_in(
        self, conversation_id: uuid.UUID
    ) -> dict[str, AgentEnvironment]:
        rows = await self._session.scalars(
            select(AgentEnvironment).where(
                AgentEnvironment.conversation_id == conversation_id
            )
        )
        return {row.agent_handle: row for row in rows}

    async def set_environment(
        self,
        conversation_id: uuid.UUID,
        agent_handle: str,
        environment: dict,
        *,
        chosen_by: dict | None,
        only_if_unset: bool = False,
    ) -> AgentEnvironment:
        """Write this agent's environment here. With ``only_if_unset`` a
        concurrent first write wins and is what comes back."""
        values = {
            "conversation_id": conversation_id,
            "agent_handle": agent_handle,
            "environment": environment,
            "chosen_by": chosen_by,
            "updated_at": datetime.now(UTC),
        }
        insert_row = insert(AgentEnvironment).values(**values)
        keys = ["conversation_id", "agent_handle"]
        if only_if_unset:
            statement = insert_row.on_conflict_do_nothing(index_elements=keys)
        else:
            statement = insert_row.on_conflict_do_update(
                index_elements=keys,
                set_={
                    "environment": environment,
                    "chosen_by": chosen_by,
                    "updated_at": values["updated_at"],
                },
            )
        await self._session.execute(statement)
        row = await self.environment(conversation_id, agent_handle)
        assert row is not None
        await self._session.refresh(row)
        return row

    async def placed_in_conversation(
        self, conversation_id: uuid.UUID
    ) -> list[AgentSession]:
        """Every session of this one conversation that is sitting on a machine,
        newest first."""
        result = await self._session.execute(
            select(AgentSession)
            .where(
                AgentSession.conversation_id == conversation_id,
                AgentSession.runtime_location.is_not(None),
            )
            .order_by(AgentSession.placed_at.desc(), AgentSession.id)
        )
        return list(result.scalars())

    async def placed_in_room(self, room_id: uuid.UUID) -> list[AgentSession]:
        """Every session in this room that is sitting on a machine, newest first.

        A room has as many as it seats agents. Callers that hold nothing but a
        room id — the transcript upload, the cleanup inventory, the executor
        admission check — ask this and then match on what they do know. The
        ordering is for the one caller that has nothing to match on: a room has
        a single pane, and it belongs to whichever session last opened one —
        `placed_at`, which only taking a machine writes.
        """
        result = await self._session.execute(
            select(AgentSession)
            .where(
                of_room(AgentSession.conversation_id, room_id),
                AgentSession.runtime_location.is_not(None),
            )
            .order_by(AgentSession.placed_at.desc(), AgentSession.id)
        )
        return list(result.scalars())

    async def last_used_on(
        self, channel: str, machines: list[str]
    ) -> dict[str, datetime]:
        """When a session of ``channel`` last ran on each of ``machines``: the
        latest write to a session row placed there, which every turn makes
        when it stores what the harness resumes by. A machine nothing ran on
        is absent."""
        if not machines:
            return {}
        machine = AgentSession.runtime_location["device_id"].as_string()
        rows = await self._session.execute(
            select(machine, func.max(AgentSession.updated_at))
            .where(
                AgentSession.runtime_location["channel"].as_string() == channel,
                machine.in_(machines),
            )
            .group_by(machine)
        )
        return {device_id: at for device_id, at in rows.all()}

    async def placed_everywhere(
        self,
    ) -> list[tuple[AgentSession, uuid.UUID, uuid.UUID]]:
        """Every placed session with its project and room, for a cold start.

        A channel re-adopts what outlived the backend, and to do that it needs
        the project each session belongs to; the room is the only thing that
        knows, so the join happens once here rather than one query per row.

        One row per (room, agent, harness) seat, not per room: a room seats as
        many agents as it has, and each one's session is re-adopted on its own
        seat. The「一间房一块屏」rule (which session owns the pane) is answered
        by ``placed_in_room``/``harness_in_room`` ordering, not by dropping the
        other seats here — a room that seats two teammates must get both back
        after a restart. A room that switched harness keeps both session rows —
        nothing clears the location of the one that stopped — and the harness
        each row names is handed on as it stands; recognising it is the
        runtime's, not the channel's, since one channel carries several of them.
        """
        room = room_column(AgentSession.conversation_id)
        result = await self._session.execute(
            select(AgentSession, Topic.project_id, room)
            .join(Topic, Topic.id == room)
            .where(AgentSession.runtime_location.is_not(None))
            .order_by(room, AgentSession.placed_at.desc(), AgentSession.id)
        )
        return [(row[0], row[1], row[2]) for row in result.all()]

    async def has_any(self, topic_id: uuid.UUID) -> bool:
        """Whether ANY agent has ever run in this room's own conversation — i.e.
        whether the room has run. Its tasks' sessions are their own.

        What froze on ``topics.session_id IS NOT NULL`` (the compute pin, the
        workspace's ``has_run``) freezes on this: the first turn is still the
        first turn, whoever took it.

        A resume token, not a bare row: taking a machine writes the row before
        the first turn has said anything, and a room that is still setting up
        has not run.
        """
        result = await self._session.execute(
            select(
                exists().where(
                    AgentSession.conversation_id == topic_id,
                    AgentSession.resume_token.is_not(None),
                )
            )
        )
        return bool(result.scalar())

    async def forget_room(self, topic_id: uuid.UUID) -> None:
        await self._session.execute(
            delete(AgentSession).where(of_room(AgentSession.conversation_id, topic_id))
        )


class AgentReadRepository:
    """Where one seat's reading of one conversation got to.

    Keyed by the seat (the authorship handle), so each teammate in a room has
    its own cursor and one seat reading a message never takes it from another.
    """

    def __init__(self, session: AsyncSession):
        self._session = session

    async def cursor(
        self, conversation_id: uuid.UUID, agent_handle: str
    ) -> tuple[datetime, uuid.UUID] | None:
        """The newest block this seat has read, or None when it has read none."""
        result = await self._session.execute(
            select(AgentRead.read_through_at, AgentRead.read_through_block_id).where(
                AgentRead.conversation_id == conversation_id,
                AgentRead.agent_handle == agent_handle,
            )
        )
        found = result.first()
        return (found[0], found[1]) if found is not None else None

    async def advance(
        self,
        *,
        conversation_id: uuid.UUID,
        agent_handle: str,
        at: datetime,
        block_id: uuid.UUID,
    ) -> None:
        """Move the seat forward to ``(at, block_id)``; never backward.

        The guarded upsert is what makes a late completion harmless: a retried
        turn finishing after a newer one must not rewind the seat and hand the
        conversation back to itself.
        """
        stmt = insert(AgentRead).values(
            conversation_id=conversation_id,
            agent_handle=agent_handle,
            read_through_at=at,
            read_through_block_id=block_id,
            updated_at=datetime.now(UTC),
        )
        await self._session.execute(
            stmt.on_conflict_do_update(
                index_elements=[
                    AgentRead.conversation_id,
                    AgentRead.agent_handle,
                ],
                set_={
                    "read_through_at": at,
                    "read_through_block_id": block_id,
                    "updated_at": datetime.now(UTC),
                },
                where=(AgentRead.read_through_at < at)
                | (
                    (AgentRead.read_through_at == at)
                    & (AgentRead.read_through_block_id < block_id)
                ),
            )
        )
