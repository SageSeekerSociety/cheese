"""Same-session room facts without the channel management service's effects."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.topic.models import Topic, TopicProgress
from app.domain.topic.repositories import TopicProgressRepository, TopicRepository


async def load_topic(session: AsyncSession, topic_id: uuid.UUID) -> Topic | None:
    """Read a room for the caller's authorized transaction; never commit."""
    return await TopicRepository(session).get(topic_id)


async def project_topics(session: AsyncSession, project_id: uuid.UUID) -> list[Topic]:
    """Read the canonical project topic order for prompt and visibility facts."""
    return list(await TopicRepository(session).list_for_project(project_id))


async def conversation_progress(
    session: AsyncSession, conversation_id: uuid.UUID
) -> TopicProgress | None:
    """Read the caller's progress row; neither flush nor commit is added."""
    return await TopicProgressRepository(session).get(conversation_id)


async def open_rooms(session: AsyncSession, room_ids: set[uuid.UUID]) -> set[uuid.UUID]:
    """The rooms among these that anyone in their project may read: neither a
    private chat nor a private channel."""
    if not room_ids:
        return set()
    rows = await session.scalars(
        select(Topic.id).where(
            Topic.id.in_(list(room_ids)),
            Topic.is_private.is_(False),
            Topic.members_only.is_(False),
        )
    )
    return set(rows)
