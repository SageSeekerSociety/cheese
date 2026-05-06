"""Space topics service.

Aligns with NT `TaskTopicsService.getSpaceHotTopics` /
`TaskTopicsService.searchSpaceTopics` (see
`cheese-backend-nt/.../task/service/TaskTopicsService.kt` and
`cheese-backend-nt/.../task/TaskTopicsRelationRepository.kt`).

Only topics linked to at least one non-deleted task in the given space are
returned. No new tables are required: `topic`, `task_topics_relation` and
`task.space_id` / `task.deleted_at` are already in place.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.task.models import Task, TaskTopicsRelation
from app.domain.topics.models import Topic


def _topic_to_dto(topic: Topic) -> dict:
    # NT Topic DTO only exposes id + name.
    return {"id": topic.id, "name": topic.name}


class SpaceTopicsService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_hot_topics(self, space_id: int, limit: int) -> list[dict]:
        """Topics with the most non-deleted tasks in the space, desc by count.

        Mirrors NT `findHotTopicsBySpaceId`.
        """
        stmt = (
            select(Topic)
            .join(TaskTopicsRelation, TaskTopicsRelation.topic_id == Topic.id)
            .join(Task, Task.id == TaskTopicsRelation.task_id)
            .where(
                Task.space_id == space_id,
                Task.deleted_at.is_(None),
                TaskTopicsRelation.deleted_at.is_(None),
                Topic.deleted_at.is_(None),
            )
            .group_by(Topic.id)
            .order_by(func.count(TaskTopicsRelation.id).desc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        topics = list(result.scalars().all())
        return [_topic_to_dto(t) for t in topics]

    async def search_topics(self, space_id: int, keyword: str, limit: int) -> list[dict]:
        """Fuzzy search topics linked to non-deleted tasks in the space.

        Mirrors NT `searchTopicsInSpace`: case-insensitive LIKE match, ordered
        by name length asc, then name asc (short & precise first).
        """
        if not keyword or not keyword.strip():
            return []

        pattern = f"%{keyword.strip()}%"
        exists_subq = (
            select(TaskTopicsRelation.id)
            .join(Task, Task.id == TaskTopicsRelation.task_id)
            .where(
                TaskTopicsRelation.topic_id == Topic.id,
                TaskTopicsRelation.deleted_at.is_(None),
                Task.space_id == space_id,
                Task.deleted_at.is_(None),
            )
            .exists()
        )
        stmt = (
            select(Topic)
            .where(
                Topic.deleted_at.is_(None),
                Topic.name.ilike(pattern),
                exists_subq,
            )
            .order_by(func.length(Topic.name).asc(), Topic.name.asc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        topics = list(result.scalars().all())
        return [_topic_to_dto(t) for t in topics]
