"""Milestone data access."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, nulls_last, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.milestone.models import Milestone, MilestoneStatus


class MilestoneRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def mark_overdue(self, project_id: uuid.UUID) -> int:
        """Lazily flip past-due upcoming milestones to missed (spec §7.2): an
        overdue milestone is 'missed', not 'upcoming'. Called on read so the
        calendar / countdown never treats a past date as 临近."""
        stmt = (
            update(Milestone)
            .where(
                Milestone.project_id == project_id,
                Milestone.status == MilestoneStatus.upcoming,
                Milestone.due_date.is_not(None),
                Milestone.due_date < datetime.now(UTC),
            )
            .values(status=MilestoneStatus.missed)
            .execution_options(synchronize_session="fetch")
        )
        result = await self._session.execute(stmt)
        # rowcount lives on the runtime CursorResult; typed as Result[Any].
        return int(getattr(result, "rowcount", 0) or 0)

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
        await self.mark_overdue(project_id)
        stmt = (
            select(Milestone)
            .where(Milestone.project_id == project_id)
            .order_by(nulls_last(Milestone.due_date.asc()), Milestone.created_at)
        )
        return list((await self._session.scalars(stmt)).all())

    async def list_calendar(self, project_id: uuid.UUID) -> list[Milestone]:
        """Upcoming milestones that have a due_date, ascending (countdown view).

        Overdue ones are flipped to missed first, so this only returns genuinely
        future milestones (next_milestone / 临近 never shows a past date)."""
        await self.mark_overdue(project_id)
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
