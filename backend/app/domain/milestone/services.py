"""Business logic for milestones."""

from datetime import datetime

from app.core.errors import BadRequestError, NotFoundError
from app.domain.milestone.models import Milestone, MilestoneKind, MilestoneStatus
from app.domain.milestone.repositories import MilestoneRepository


class MilestoneService:
    def __init__(self, repo: MilestoneRepository) -> None:
        self._repo = repo

    async def _require(self, milestone_id: int) -> Milestone:
        milestone = await self._repo.get_by_id(milestone_id)
        if milestone is None:
            raise NotFoundError(f"Milestone {milestone_id} not found")
        return milestone

    async def create_milestone(
        self,
        *,
        project_id: int,
        title: str,
        due_at: datetime,
        created_by_id: int,
        description: str = "",
        kind: MilestoneKind = MilestoneKind.NORMAL,
    ) -> Milestone:
        if not title.strip():
            raise BadRequestError("milestone title is required")
        return await self._repo.create(
            project_id=project_id,
            title=title,
            due_at=due_at,
            created_by_id=created_by_id,
            description=description,
            kind=kind,
        )

    async def get_milestone(self, milestone_id: int) -> Milestone:
        return await self._require(milestone_id)

    async def list_by_project(self, project_id: int) -> list[Milestone]:
        return await self._repo.list_by_project(project_id)

    async def list_overdue(self, project_id: int, *, now: datetime) -> list[Milestone]:
        return await self._repo.list_open_due_before(project_id, now)

    async def mark_done(self, milestone_id: int) -> Milestone:
        milestone = await self._require(milestone_id)
        return await self._repo.set_status(milestone, MilestoneStatus.DONE)

    async def cancel(self, milestone_id: int) -> Milestone:
        milestone = await self._require(milestone_id)
        return await self._repo.set_status(milestone, MilestoneStatus.CANCELLED)
