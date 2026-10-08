"""Same-session room facts without the channel management service's effects."""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.topic.models import Topic
from app.domain.topic.repositories import TopicRepository


async def load_topic(session: AsyncSession, topic_id: uuid.UUID) -> Topic | None:
    """Read a room for the caller's authorized transaction; never commit."""
    return await TopicRepository(session).get(topic_id)
