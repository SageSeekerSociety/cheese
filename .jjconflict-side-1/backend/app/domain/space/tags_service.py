"""Space tags service (知是 标签).

Aligns with NT `TaskTopicsService.getSpaceHotTopics` /
`TaskTopicsService.searchSpaceTopics` (see
`cheese-backend-nt/.../task/service/TaskTopicsService.kt` and
`cheese-backend-nt/.../task/TaskTopicsRelationRepository.kt`).

Only topics linked to at least one non-deleted task in the given space are
returned. No new tables are required: `tag`, `task_tag_relation` and
`task.space_id` / `task.deleted_at` are already in place.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.tag.models import Tag
from app.domain.task.models import Task, TaskTagRelation


def _tag_to_dto(tag: Tag) -> dict:
    # NT Topic DTO only exposes id + name.
    return {"id": tag.id, "name": tag.name}


class SpaceTagsService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_hot_topics(self, space_id: int, limit: int) -> list[dict]:
        """Topics with the most non-deleted tasks in the space, desc by count.

        Mirrors NT `findHotTopicsBySpaceId`.
        """
        stmt = (
            select(Tag)
            .join(TaskTagRelation, TaskTagRelation.tag_id == Tag.id)
            .join(Task, Task.id == TaskTagRelation.task_id)
            .where(
                Task.space_id == space_id,
                Task.deleted_at.is_(None),
                TaskTagRelation.deleted_at.is_(None),
                Tag.deleted_at.is_(None),
            )
            .group_by(Tag.id)
            .order_by(func.count(TaskTagRelation.id).desc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        topics = list(result.scalars().all())
        return [_tag_to_dto(t) for t in topics]

    async def search_topics(
        self, space_id: int, keyword: str, limit: int
    ) -> list[dict]:
        """Fuzzy search topics linked to non-deleted tasks in the space.

        Mirrors NT `searchTopicsInSpace`: case-insensitive LIKE match, ordered
        by name length asc, then name asc (short & precise first).
        """
        if not keyword or not keyword.strip():
            return []

        pattern = f"%{keyword.strip()}%"
        exists_subq = (
            select(TaskTagRelation.id)
            .join(Task, Task.id == TaskTagRelation.task_id)
            .where(
                TaskTagRelation.tag_id == Tag.id,
                TaskTagRelation.deleted_at.is_(None),
                Task.space_id == space_id,
                Task.deleted_at.is_(None),
            )
            .exists()
        )
        stmt = (
            select(Tag)
            .where(
                Tag.deleted_at.is_(None),
                Tag.name.ilike(pattern),
                exists_subq,
            )
            .order_by(func.length(Tag.name).asc(), Tag.name.asc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        topics = list(result.scalars().all())
        return [_tag_to_dto(t) for t in topics]
