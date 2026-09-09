"""Agent session data access."""

import uuid

from sqlalchemy import delete, exists, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.agent_session.models import AgentSession


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

    async def resume_token(self, topic_id: uuid.UUID, agent_handle: str) -> str | None:
        """What this agent resumes its conversation in this place by."""
        result = await self._session.execute(
            select(AgentSession.resume_token).where(
                *self._at(topic_id),
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

        The conflict target names a PARTIAL unique index by repeating its
        predicate: `task_id` is NULL on every room row, NULL is not equal to
        NULL in a unique index, so an index over the pair would not enforce
        anything at all.
        """
        stmt = insert(AgentSession).values(
            topic_id=topic_id,
            agent_handle=agent_handle,
            resume_token=resume_token,
        )
        await self._session.execute(
            stmt.on_conflict_do_update(
                index_elements=[AgentSession.topic_id, AgentSession.agent_handle],
                index_where=text("task_id IS NULL"),
                set_={"resume_token": stmt.excluded.resume_token},
            )
        )
        await self._session.flush()

    async def has_any(self, topic_id: uuid.UUID) -> bool:
        """Whether ANY agent has ever run here — i.e. whether the place has run.

        What froze on ``topics.session_id IS NOT NULL`` (the compute pin, the
        workspace's ``has_run``) freezes on this: the first turn is still the
        first turn, whoever took it.
        """
        result = await self._session.execute(
            select(exists().where(*self._at(topic_id)))
        )
        return bool(result.scalar())

    async def forget_room(self, topic_id: uuid.UUID) -> None:
        await self._session.execute(
            delete(AgentSession).where(AgentSession.topic_id == topic_id)
        )
