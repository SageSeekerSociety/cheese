"""Agent session data access."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import delete, exists, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.agent_session.models import AgentSession
from app.domain.topic.models import Topic


class AgentSessionRepository:
    """Keyed by the PLACE a conversation happened in.

    Callers hand over one id — a room's or a thread's — because that is how the
    rest of the platform addresses a place. The pair is resolved here, once per
    call, and stored as (room, thread) so a room and each of its threads keep
    conversations that cannot be mistaken for one another.
    """

    def __init__(self, session: AsyncSession):
        self._session = session

    @staticmethod
    def _at(room_id: uuid.UUID):
        """One room's own conversations.

        `task_id IS NULL` is not redundant with the room clause: rows a thread
        wrote back when work was a place still sit under the same `topic_id`,
        and leaving them in would resume the room's session from a card's
        conversation.
        """
        return (
            AgentSession.topic_id == room_id,
            AgentSession.task_id.is_(None),
        )

    async def resume_token(
        self, topic_id: uuid.UUID, agent_handle: str, harness: str
    ) -> str | None:
        """What this agent resumes its conversation in this place by."""
        result = await self._session.execute(
            select(AgentSession.resume_token).where(
                *self._at(topic_id),
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

        The conflict target names a PARTIAL unique index by repeating its
        predicate: `task_id` is NULL on every room row, NULL is not equal to
        NULL in a unique index, so an index over the pair would not enforce
        anything at all.
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
        """
        await self._upsert(
            topic_id=topic_id,
            agent_handle=agent_handle,
            harness=harness,
            values={"work_lease": work_lease, "runtime_location": runtime_location},
        )

    async def _upsert(
        self, *, topic_id: uuid.UUID, agent_handle: str, harness: str, values: dict
    ) -> None:
        # `updated_at` is stamped here by hand: ON CONFLICT DO UPDATE writes
        # exactly the columns named in `set_`, so the mapper's `onupdate` never
        # fires and the row would go on saying it was last touched when it was
        # created. Which session opened the room's one pane is read off this.
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
                index_where=text("task_id IS NULL"),
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
                *self._at(topic_id),
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
        a single pane, and it belongs to whichever session last opened one.
        """
        result = await self._session.execute(
            select(AgentSession)
            .where(*self._at(room_id), AgentSession.runtime_location.is_not(None))
            .order_by(AgentSession.updated_at.desc(), AgentSession.id)
        )
        return list(result.scalars())

    async def placed_everywhere(self) -> list[tuple[AgentSession, uuid.UUID]]:
        """Every placed session with its project, for a channel's cold start.

        A channel re-adopts what outlived the backend, and to do that it needs
        the project each session belongs to; the room is the only thing that
        knows, so the join happens once here rather than one query per row.
        """
        result = await self._session.execute(
            select(AgentSession, Topic.project_id)
            .join(Topic, Topic.id == AgentSession.topic_id)
            .where(
                AgentSession.task_id.is_(None),
                AgentSession.runtime_location.is_not(None),
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
                    *self._at(topic_id), AgentSession.resume_token.is_not(None)
                )
            )
        )
        return bool(result.scalar())

    async def forget_room(self, topic_id: uuid.UUID) -> None:
        await self._session.execute(
            delete(AgentSession).where(AgentSession.topic_id == topic_id)
        )
