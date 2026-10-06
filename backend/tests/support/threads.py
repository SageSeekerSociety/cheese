"""A 支线 in a test: where 芝士 answers once someone calls it."""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.block.models import AuthorType
from app.domain.block.repositories import BlockRepository
from app.domain.thread.services import open_thread
from app.domain.topic.models import Topic


async def thread_in(
    session: AsyncSession, room: Topic, author: str = "alice"
) -> uuid.UUID:
    """A 支线 under a message ``author`` said in ``room``'s main line, the way
    a person opens one by replying under it. The caller commits."""
    said = await BlockRepository(session).add(
        project_id=room.project_id,
        conversation_id=room.id,
        author=author,
        author_type=AuthorType.participant,
        content="这件事在支线里说",
    )
    return (await open_thread(session, said.id, by=author)).id
