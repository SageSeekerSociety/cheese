"""Block-backed activity signals for callers that judge conversation liveness."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select

from app.core.db import SessionFactory
from app.domain.block.models import Block


async def last_block_at(
    sessions: SessionFactory, topic_ids: set[uuid.UUID]
) -> dict[uuid.UUID, datetime]:
    """Newest block timestamp per conversation, absent when there are no blocks.

    A liveness caller can compare this with the same last activity a person
    sees in the room. All returned timestamps are timezone-aware.
    """
    if not topic_ids:
        return {}
    async with sessions() as session:
        rows = (
            await session.execute(
                select(Block.conversation_id, func.max(Block.created_at))
                .where(Block.conversation_id.in_(topic_ids))
                .group_by(Block.conversation_id)
            )
        ).all()
    out: dict[uuid.UUID, datetime] = {}
    for topic_id, last in rows:
        if last is None:
            continue
        out[topic_id] = last if last.tzinfo is not None else last.replace(tzinfo=UTC)
    return out
