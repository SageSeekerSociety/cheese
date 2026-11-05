"""Commit the session-opening facts after transport acceptance."""

import uuid
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.agent.queries import _agent_at
from app.domain.agent_instance.services import AgentInstanceService
from app.domain.agent_session.services import AgentSessionService
from app.domain.project.repositories import ProjectRepository
from app.domain.room_task.place import PlaceResolver


class DeliveryStamp(Protocol):
    async def __call__(
        self, db: AsyncSession, *, turn_id: uuid.UUID, at: datetime
    ) -> None: ...


async def remember_opening(
    sessions: async_sessionmaker[AsyncSession],
    *,
    topic_id: uuid.UUID,
    agent_handle: str,
    harness: str,
    digests: dict[str, str],
    was: dict[str, str] | None,
) -> None:
    async with sessions() as session:
        await AgentSessionService(session).remember_told(
            conversation_id=topic_id,
            agent_handle=agent_handle,
            harness=harness,
            told=digests,
            was=was,
        )
        await session.commit()


async def forget_opening(
    sessions: async_sessionmaker[AsyncSession],
    *,
    conversation_id: uuid.UUID,
    agent_handle: str,
    harness: str,
) -> None:
    """The conversation was compacted: the snapshot it was told sat in the
    history the compaction replaced with a summary, so nothing it was told can
    be counted on, and its next input carries the whole snapshot again."""
    async with sessions() as session:
        await AgentSessionService(session).forget_told(
            conversation_id=conversation_id,
            agent_handle=agent_handle,
            harness=harness,
        )
        await session.commit()


async def abandon_opening(
    sessions: async_sessionmaker[AsyncSession],
    *,
    conversation_id: uuid.UUID,
    seat: str,
    harness: str,
) -> None:
    """The conversation cannot be compacted, so it cannot go on: the next turn
    opens a new one (`forget_conversation`) and, with what it was told gone,
    carries the whole project state again.

    ``seat`` is who ACTED — the handle the event names. The row is keyed by the
    agent that seat belongs to, the key `_save_session_pointer` writes it under
    and the next turn reads it back under; written under the seat it matches no
    row and nothing happens. Resolved here the same way, once, so the caller
    does not have to know.

    这一条只在「确定走不通」时用一次——压缩本身就是那条把对话缩小的路，它
    断了，再发同一条只是把同一段装不下的历史再送一遍。它不重试，也不换模型：
    下一轮是一条新会话，任何还在文件、任务、实况文档里的东西都还在，对话里
    没落下来的那部分就此结束（房间里的消息一条不少）。"""
    async with sessions() as session:
        place = await PlaceResolver(session).conversation(conversation_id)
        if place is None:
            return
        owner = await ProjectRepository(session).get(place.project_id)
        agent = (
            await AgentInstanceService(session).for_seat_handle(owner, seat)
            if owner is not None
            else None
        ) or await _agent_at(session, place)
        if agent is None:
            return
        await AgentSessionService(session).forget_conversation(
            conversation_id=conversation_id,
            agent_handle=agent.handle,
            harness=harness,
        )
        await session.commit()


async def commit_delivery(
    sessions: async_sessionmaker[AsyncSession],
    *,
    stamp: DeliveryStamp,
    turn_id: uuid.UUID,
) -> None:
    async with sessions() as session:
        await stamp(session, turn_id=turn_id, at=datetime.now(UTC))
        await session.commit()
