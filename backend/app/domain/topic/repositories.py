"""Topic data access."""

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.topic.models import Topic, TopicKind


class TopicRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def add(
        self,
        *,
        project_id: uuid.UUID,
        title: str,
        parent_id: uuid.UUID | None = None,
        kind: TopicKind = TopicKind.topic,
        created_by: str | None = None,
        upgraded_from_block_id: uuid.UUID | None = None,
    ) -> Topic:
        topic = Topic(
            project_id=project_id,
            title=title,
            parent_id=parent_id,
            kind=kind,
            created_by=created_by,
            upgraded_from_block_id=upgraded_from_block_id,
        )
        self._session.add(topic)
        await self._session.flush()
        await self._session.refresh(topic)
        return topic

    async def get(self, topic_id: uuid.UUID) -> Topic | None:
        return await self._session.get(Topic, topic_id)

    async def list_for_project(self, project_id: uuid.UUID) -> list[Topic]:
        # Private chats are not part of the topic tree.
        stmt = (
            select(Topic)
            .where(Topic.project_id == project_id, Topic.is_private.is_(False))
            .order_by(Topic.created_at)
        )
        return list((await self._session.scalars(stmt)).all())

    async def get_or_create_private(
        self, *, project_id: uuid.UUID, user_handle: str
    ) -> Topic:
        """The member's 1:1 private chat with 芝士 in this project."""
        stmt = select(Topic).where(
            Topic.project_id == project_id,
            Topic.is_private.is_(True),
            Topic.private_owner == user_handle,
        )
        existing = (await self._session.scalars(stmt)).first()
        if existing is not None:
            return existing
        topic = Topic(
            project_id=project_id,
            title=f"与芝士私聊 · {user_handle}",
            kind=TopicKind.topic,
            created_by=user_handle,
            is_private=True,
            private_owner=user_handle,
        )
        self._session.add(topic)
        await self._session.flush()
        await self._session.refresh(topic)
        return topic

    async def list_children(self, parent_id: uuid.UUID) -> list[Topic]:
        stmt = (
            select(Topic).where(Topic.parent_id == parent_id).order_by(Topic.created_at)
        )
        return list((await self._session.scalars(stmt)).all())

    async def count_for_project(self, project_id: uuid.UUID) -> int:
        stmt = (
            select(func.count())
            .select_from(Topic)
            .where(Topic.project_id == project_id, Topic.is_private.is_(False))
        )
        return int((await self._session.scalar(stmt)) or 0)

    async def set_session_id(self, topic: Topic, session_id: str) -> None:
        topic.session_id = session_id
        await self._session.flush()
