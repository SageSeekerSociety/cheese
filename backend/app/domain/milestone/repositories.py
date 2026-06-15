"""Milestone data access."""

import uuid

from sqlalchemy import func, nulls_last, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.milestone.models import Milestone, MilestoneStatus


class MilestoneRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def add(
        self,
        *,
        project_id: uuid.UUID,
        title: str,
        description: str = "",
        due_date=None,
        source_topic_id: uuid.UUID | None = None,
        auto_pinned: bool = False,
    ) -> Milestone:
        milestone = Milestone(
            project_id=project_id,
            title=title,
            description=description,
            due_date=due_date,
            source_topic_id=source_topic_id,
            auto_pinned=auto_pinned,
        )
        self._session.add(milestone)
        await self._session.flush()
        await self._session.refresh(milestone)
        return milestone

    async def get(self, milestone_id: uuid.UUID) -> Milestone | None:
        return await self._session.get(Milestone, milestone_id)

    async def list_for_project(self, project_id: uuid.UUID) -> list[Milestone]:
        """All milestones, ordered by due_date ascending with nulls last."""
        stmt = (
            select(Milestone)
            .where(Milestone.project_id == project_id)
            .order_by(nulls_last(Milestone.due_date.asc()), Milestone.created_at)
        )
        return list((await self._session.scalars(stmt)).all())

    async def list_calendar(self, project_id: uuid.UUID) -> list[Milestone]:
        """Upcoming milestones that have a due_date, ascending (countdown view)."""
        stmt = (
            select(Milestone)
            .where(
                Milestone.project_id == project_id,
                Milestone.status == MilestoneStatus.upcoming,
                Milestone.due_date.is_not(None),
            )
            .order_by(Milestone.due_date.asc())
        )
        return list((await self._session.scalars(stmt)).all())

    async def count_for_project(self, project_id: uuid.UUID) -> int:
        stmt = (
            select(func.count())
            .select_from(Milestone)
            .where(Milestone.project_id == project_id)
        )
        return int((await self._session.scalar(stmt)) or 0)

    async def save(self, milestone: Milestone) -> Milestone:
        await self._session.flush()
        await self._session.refresh(milestone)
        return milestone

    async def delete(self, milestone: Milestone) -> None:
        await self._session.delete(milestone)
        await self._session.flush()
