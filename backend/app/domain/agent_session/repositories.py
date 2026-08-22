"""Agent session data access."""

import uuid

from sqlalchemy import exists, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.agent_session.models import AgentSession
from app.domain.room_task.place import room_and_task


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
    def _at(room_id: uuid.UUID, task_id: uuid.UUID | None):
        return (
            AgentSession.topic_id == room_id,
            AgentSession.task_id.is_(None)
            if task_id is None
            else AgentSession.task_id == task_id,
        )

    async def resume_token(self, topic_id: uuid.UUID, agent_handle: str) -> str | None:
        """What this agent resumes its conversation in this place by."""
        room_id, task_id = await room_and_task(self._session, topic_id)
        result = await self._session.execute(
            select(AgentSession.resume_token).where(
                *self._at(room_id, task_id),
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

        The conflict target names one of two PARTIAL unique indexes by repeating
        its predicate. There is no single index over the pair to name: `task_id`
        is NULL on every room row, NULL is not equal to NULL in a unique index,
        and one wider index would therefore stop enforcing the room half at all.
        """
        room_id, task_id = await room_and_task(self._session, topic_id)
        room_half = task_id is None
        stmt = insert(AgentSession).values(
            topic_id=room_id,
            task_id=task_id,
            agent_handle=agent_handle,
            resume_token=resume_token,
        )
        await self._session.execute(
            stmt.on_conflict_do_update(
                index_elements=[AgentSession.topic_id, AgentSession.agent_handle]
                if room_half
                else [AgentSession.task_id, AgentSession.agent_handle],
                index_where=text("task_id IS NULL")
                if room_half
                else text("task_id IS NOT NULL"),
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
        room_id, task_id = await room_and_task(self._session, topic_id)
        result = await self._session.execute(
            select(exists().where(*self._at(room_id, task_id)))
        )
        return bool(result.scalar())
