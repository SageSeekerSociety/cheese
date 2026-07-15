"""Milestone business logic."""

import uuid
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.domain.milestone.models import Milestone, MilestoneStatus
from app.domain.milestone.repositories import MilestoneRepository
from app.domain.project.repositories import ProjectRepository


class MilestoneService:
    def __init__(self, session: AsyncSession):
        self._repo = MilestoneRepository(session)
        self._projects = ProjectRepository(session)

    async def create(
        self,
        *,
        project_id: uuid.UUID,
        title: str,
        description: str = "",
        due_date: datetime | None = None,
        source_topic_id: uuid.UUID | None = None,
        auto_pinned: bool = False,
    ) -> Milestone:
        if await self._projects.get(project_id) is None:
            raise NotFoundError("Project not found")
        return await self._repo.add(
            project_id=project_id,
            title=title,
            description=description,
            due_date=due_date,
            source_topic_id=source_topic_id,
            auto_pinned=auto_pinned,
        )

    async def get_or_404(self, milestone_id: uuid.UUID) -> Milestone:
        milestone = await self._repo.get(milestone_id)
        if milestone is None:
            raise NotFoundError("Milestone not found")
        return milestone

    async def list_for_project(
        self, project_id: uuid.UUID
    ) -> tuple[list[Milestone], int]:
        if await self._projects.get(project_id) is None:
            raise NotFoundError("Project not found")
        return (
            await self._repo.list_for_project(project_id),
            await self._repo.count_for_project(project_id),
        )

    async def calendar(self, project_id: uuid.UUID) -> list[Milestone]:
        if await self._projects.get(project_id) is None:
            raise NotFoundError("Project not found")
        return await self._repo.list_calendar(project_id)

    async def update(
        self,
        milestone_id: uuid.UUID,
        *,
        title: str | None = None,
        description: str | None = None,
        due_date: datetime | None = None,
        status: MilestoneStatus | None = None,
        due_date_set: bool = False,
    ) -> Milestone:
        milestone = await self.get_or_404(milestone_id)
        if title is not None:
            milestone.title = title
        if description is not None:
            milestone.description = description
        if due_date_set:
            milestone.due_date = due_date
        if status is not None:
            milestone.status = status
        return await self._repo.save(milestone)

    async def delete(self, milestone_id: uuid.UUID) -> None:
        milestone = await self.get_or_404(milestone_id)
        await self._repo.delete(milestone)
