"""Agent session data access."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import delete, exists, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.agent_session.models import AgentSession
from app.domain.topic.models import Topic


class AgentSessionRepository:
    """Keyed by the ROOM a conversation happened in.

    One address and one only: a piece of work is a subagent inside a room's
    session, not a second session of its own (结论 31、43), so every row here
    belongs to a room and nothing narrower.
    """

    def __init__(self, session: AsyncSession):
        self._session = session

    async def ensure(
        self, topic_id: uuid.UUID, agent_handle: str, harness: str
    ) -> AgentSession:
        await self._upsert(
            topic_id=topic_id, agent_handle=agent_handle, harness=harness, values={}
        )
        row = await self.get(topic_id, agent_handle, harness)
        assert row is not None
        return row

    async def by_id(self, session_id: uuid.UUID, *, lock: bool = False):
        query = select(AgentSession).where(AgentSession.id == session_id)
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        return await self._session.scalar(query)

    async def resume_token(
        self, topic_id: uuid.UUID, agent_handle: str, harness: str
    ) -> str | None:
        """What this agent resumes its conversation in this place by."""
        result = await self._session.execute(
            select(AgentSession.resume_token).where(
                AgentSession.topic_id == topic_id,
                AgentSession.agent_handle == agent_handle,
                AgentSession.harness == harness,
            )
        )
        return result.scalar_one_or_none()

    async def save(
        self, *, topic_id: uuid.UUID, agent_handle: str, resume_token: str, harness: str
    ) -> None:
        """Record where this agent's conversation got to (upsert).

        ON CONFLICT rather than get-then-insert: two turns can reach here at once
        (a resumed turn and the sweep's own resume are the pair that actually
        does), and losing that race must overwrite, not raise.
        """
        await self._upsert(
            topic_id=topic_id,
            agent_handle=agent_handle,
            harness=harness,
            values={"resume_token": resume_token},
        )

    async def save_place(
        self,
        *,
        topic_id: uuid.UUID,
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
            topic_id=topic_id,
            agent_handle=agent_handle,
            harness=harness,
            values={
                "work_lease": work_lease,
                "runtime_location": runtime_location,
                "placed_at": datetime.now(UTC),
            },
        )

    async def _upsert(
        self, *, topic_id: uuid.UUID, agent_handle: str, harness: str, values: dict
    ) -> None:
        # `updated_at` is stamped here by hand: ON CONFLICT DO UPDATE writes
        # exactly the columns named in `set_`, so the mapper's `onupdate` never
        # fires and the row would go on saying it was last touched when it was
        # created. It says exactly that and nothing more — which session opened
        # the room's pane is `placed_at`, written only by `save_place`.
        values = {**values, "updated_at": datetime.now(UTC)}
        stmt = insert(AgentSession).values(
            topic_id=topic_id,
            agent_handle=agent_handle,
            harness=harness,
            **values,
        )
        await self._session.execute(
            stmt.on_conflict_do_update(
                index_elements=[
                    AgentSession.topic_id,
                    AgentSession.agent_handle,
                    AgentSession.harness,
                ],
                set_={key: getattr(stmt.excluded, key) for key in values},
            )
        )
        await self._session.flush()

    async def get(
        self, topic_id: uuid.UUID, agent_handle: str, harness: str
    ) -> AgentSession | None:
        """This agent's session row in this place, if it has one."""
        result = await self._session.execute(
            select(AgentSession).where(
                AgentSession.topic_id == topic_id,
                AgentSession.agent_handle == agent_handle,
                AgentSession.harness == harness,
            )
        )
        return result.scalar_one_or_none()

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
                AgentSession.topic_id == room_id,
                AgentSession.runtime_location.is_not(None),
            )
            .order_by(AgentSession.placed_at.desc(), AgentSession.id)
        )
        return list(result.scalars())

    async def placed_everywhere(self) -> list[tuple[AgentSession, uuid.UUID]]:
        """Each room's last-placed session with its project, for a cold start.

        A channel re-adopts what outlived the backend, and to do that it needs
        the project each session belongs to; the room is the only thing that
        knows, so the join happens once here rather than one query per row.

        One row per room, and the same rule ``placed_in_room`` and
        ``harness_in_room`` already answer by: a room has one screen and it
        belongs to whichever session last opened one. A room that switched
        harness keeps both session rows — nothing clears the location of the
        one that stopped — and only the last-placed one comes back here. The
        harness that row names is handed on as it stands; recognising it is the
        runtime's, not the channel's, since one channel carries several of them.
        """
        result = await self._session.execute(
            select(AgentSession, Topic.project_id)
            .join(Topic, Topic.id == AgentSession.topic_id)
            .where(AgentSession.runtime_location.is_not(None))
            .distinct(AgentSession.topic_id)
            .order_by(
                AgentSession.topic_id,
                AgentSession.placed_at.desc(),
                AgentSession.id,
            )
        )
        return [(row[0], row[1]) for row in result.all()]

    async def has_any(self, topic_id: uuid.UUID) -> bool:
        """Whether ANY agent has ever run here — i.e. whether the place has run.

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
                    AgentSession.topic_id == topic_id,
                    AgentSession.resume_token.is_not(None),
                )
            )
        )
        return bool(result.scalar())

    async def forget_room(self, topic_id: uuid.UUID) -> None:
        await self._session.execute(
            delete(AgentSession).where(AgentSession.topic_id == topic_id)
        )
