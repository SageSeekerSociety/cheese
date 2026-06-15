"""Project data access."""

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.project.models import (
    AiMode,
    Project,
    ProjectTaskLink,
)


class ProjectRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def add(
        self,
        *,
        name: str,
        owner_handle: str | None = None,
        ai_mode: AiMode = AiMode.collaborative,
        expert_role: str | None = None,
    ) -> Project:
        project = Project(
            name=name,
            owner_handle=owner_handle,
            ai_mode=ai_mode,
            expert_role=expert_role,
        )
        self._session.add(project)
        await self._session.flush()
        await self._session.refresh(project)
        return project

    async def get(self, project_id: uuid.UUID) -> Project | None:
        return await self._session.get(Project, project_id)

    async def list_all(self) -> list[Project]:
        stmt = select(Project).order_by(Project.created_at.desc())
        return list((await self._session.scalars(stmt)).all())

    async def count(self) -> int:
        return int(
            (await self._session.scalar(select(func.count()).select_from(Project))) or 0
        )

    async def set_root_topic(self, project: Project, root_topic_id: uuid.UUID) -> None:
        project.root_topic_id = root_topic_id
        await self._session.flush()

    async def set_summary(self, project: Project, summary: str) -> None:
        project.summary = summary
        await self._session.flush()

    async def link_task(
        self, *, project_id: uuid.UUID, task_id: uuid.UUID
    ) -> ProjectTaskLink:
        link = ProjectTaskLink(project_id=project_id, task_id=task_id)
        self._session.add(link)
        await self._session.flush()
        await self._session.refresh(link)
        return link

    async def get_link(
        self, *, project_id: uuid.UUID, task_id: uuid.UUID
    ) -> ProjectTaskLink | None:
        stmt = select(ProjectTaskLink).where(
            ProjectTaskLink.project_id == project_id,
            ProjectTaskLink.task_id == task_id,
        )
        return (await self._session.scalars(stmt)).first()

    async def list_links(self, project_id: uuid.UUID) -> list[ProjectTaskLink]:
        stmt = select(ProjectTaskLink).where(ProjectTaskLink.project_id == project_id)
        return list((await self._session.scalars(stmt)).all())

    async def list_projects_for_task(self, task_id: uuid.UUID) -> list[ProjectTaskLink]:
        stmt = select(ProjectTaskLink).where(ProjectTaskLink.task_id == task_id)
        return list((await self._session.scalars(stmt)).all())
