"""Data access for milestones. No business logic."""

from datetime import UTC, datetime

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.milestone.models import Milestone, MilestoneKind, MilestoneStatus


class MilestoneRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        project_id: int,
        title: str,
        due_at: datetime,
        created_by_id: int,
        description: str = "",
        kind: MilestoneKind = MilestoneKind.NORMAL,
    ) -> Milestone:
        now = datetime.now(UTC)
        milestone = Milestone(
            project_id=project_id,
            title=title,
            description=description,
            due_at=due_at,
            kind=kind.value,
            status=MilestoneStatus.OPEN.value,
            created_by_id=created_by_id,
            created_at=now,
            updated_at=now,
            deleted_at=None,
        )
        self._session.add(milestone)
        await self._session.flush()
        return milestone

    async def get_by_id(self, milestone_id: int) -> Milestone | None:
        stmt: Select[tuple[Milestone]] = select(Milestone).where(
            Milestone.id == milestone_id, Milestone.deleted_at.is_(None)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_by_project(self, project_id: int) -> list[Milestone]:
        """All milestones of a project, earliest due first."""
        stmt: Select[tuple[Milestone]] = (
            select(Milestone)
            .where(Milestone.project_id == project_id, Milestone.deleted_at.is_(None))
            .order_by(Milestone.due_at.asc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list_open_due_before(self, project_id: int, before: datetime) -> list[Milestone]:
        """Open milestones due before ``before`` — the deadline倒排 query."""
        stmt: Select[tuple[Milestone]] = (
            select(Milestone)
            .where(
                Milestone.project_id == project_id,
                Milestone.status == MilestoneStatus.OPEN.value,
                Milestone.due_at < before,
                Milestone.deleted_at.is_(None),
            )
            .order_by(Milestone.due_at.asc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def set_status(self, milestone: Milestone, status: MilestoneStatus) -> Milestone:
        milestone.status = status.value
        milestone.updated_at = datetime.now(UTC)
        await self._session.flush()
        return milestone
