"""Timeline operations shared by domain services."""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.block.models import AuthorType, Block, BlockKind
from app.domain.block.repositories import BlockRepository


async def record_system_event(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    topic_id: uuid.UUID,
    content: str,
    meta: dict | None = None,
) -> Block:
    """Record a platform event within the caller's transaction and room lock."""
    return await BlockRepository(session).add(
        project_id=project_id,
        topic_id=topic_id,
        author="system",
        author_type=AuthorType.system,
        kind=BlockKind.event,
        content=content,
        meta=meta,
    )
