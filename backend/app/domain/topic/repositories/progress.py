"""进度层 storage: one checklist per conversation — a room or a task (#187).

话题目录里唯一一个不是转发的小类：两个方法，一张表，没有共用判据，不值得再拆
出门面。
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.topic.models import TopicProgress


class TopicProgressRepository:
    """进度层 storage: one checklist per conversation — a room or a task (#187)."""

    def __init__(self, session: AsyncSession):
        self._session = session

    async def get(self, conversation_id: uuid.UUID) -> TopicProgress | None:
        stmt = select(TopicProgress).where(
            TopicProgress.conversation_id == conversation_id
        )
        return (await self._session.scalars(stmt)).first()

    async def save(
        self,
        conversation_id: uuid.UUID,
        items: list[dict],
        *,
        turn_id: uuid.UUID | None = None,
    ) -> TopicProgress:
        """Overwrite this conversation's checklist (upsert).

        Current state, not history — the conversation timeline is where history
        lives. Callers hand over a fresh list each time; the row is rewritten so
        a reader never sees a half-applied checklist.
        """
        row = await self.get(conversation_id)
        if row is None:
            row = TopicProgress(
                conversation_id=conversation_id, items=[], turn_id=turn_id
            )
            self._session.add(row)
        # Rebind rather than mutate: SQLAlchemy does not track in-place edits of
        # a plain JSON column, so an appended item would silently not be saved.
        row.items = [dict(item) for item in items]
        row.turn_id = turn_id
        await self._session.flush()
        return row
