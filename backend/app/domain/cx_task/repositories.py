"""Task Template & Task data access."""

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.project.models import Project
from app.domain.space.models import Space
from app.domain.cx_task.models import Task, TaskApplication, TaskTemplate


def _escape_like(term: str) -> str:
    """Escape LIKE wildcards so a user keyword matches literally."""
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class TaskTemplateRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def add(
        self,
        *,
        space_id: int,
        name: str,
        description: str,
        resource_pack: dict,
        conditions: list[dict],
        default_role: str | None,
    ) -> TaskTemplate:
        template = TaskTemplate(
            space_id=space_id,
            name=name,
            description=description,
            resource_pack=resource_pack,
            conditions=conditions,
            default_role=default_role,
        )
        self._session.add(template)
        await self._session.flush()
        await self._session.refresh(template)
        return template

    async def get(self, template_id: uuid.UUID) -> TaskTemplate | None:
        return await self._session.get(TaskTemplate, template_id)

    async def list_for_space(self, space_id: int) -> list[TaskTemplate]:
        stmt = (
            select(TaskTemplate)
            .where(TaskTemplate.space_id == space_id)
            .order_by(TaskTemplate.created_at.desc())
        )
        return list((await self._session.scalars(stmt)).all())

    async def count_for_space(self, space_id: int) -> int:
        stmt = (
            select(func.count())
            .select_from(TaskTemplate)
            .where(TaskTemplate.space_id == space_id)
        )
        return int((await self._session.scalar(stmt)) or 0)

    async def list_published(
        self, *, query: str | None = None
    ) -> list[tuple[TaskTemplate, str]]:
        """Market catalog: published templates + their Space name, newest first.

        `query` filters by keyword on template name/description or Space name
        (parameterized ILIKE — never string-built SQL).
        """
        stmt = (
            select(TaskTemplate, Space.name)
            .join(Space, TaskTemplate.space_id == Space.id)
            .where(TaskTemplate.published.is_(True))
            .order_by(TaskTemplate.created_at.desc())
        )
        if query:
            pattern = f"%{_escape_like(query)}%"
            stmt = stmt.where(
                TaskTemplate.name.ilike(pattern, escape="\\")
                | TaskTemplate.description.ilike(pattern, escape="\\")
                | Space.name.ilike(pattern, escape="\\")
            )
        rows = (await self._session.execute(stmt)).all()
        return [(template, space_name) for template, space_name in rows]


class TaskApplicationRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def add(
        self, *, template_id: uuid.UUID, project_id: uuid.UUID, pitch: str
    ) -> TaskApplication:
        application = TaskApplication(
            template_id=template_id, project_id=project_id, pitch=pitch
        )
        self._session.add(application)
        await self._session.flush()
        await self._session.refresh(application)
        return application

    async def get(self, application_id: uuid.UUID) -> TaskApplication | None:
        return await self._session.get(TaskApplication, application_id)

    async def get_for(
        self, *, template_id: uuid.UUID, project_id: uuid.UUID
    ) -> TaskApplication | None:
        stmt = select(TaskApplication).where(
            TaskApplication.template_id == template_id,
            TaskApplication.project_id == project_id,
        )
        return await self._session.scalar(stmt)

    async def list_for_template(
        self, template_id: uuid.UUID
    ) -> list[tuple[TaskApplication, str]]:
        """Applications on a template + the applying project's name, oldest first."""
        stmt = (
            select(TaskApplication, Project.name)
            .join(Project, TaskApplication.project_id == Project.id)
            .where(TaskApplication.template_id == template_id)
            .order_by(TaskApplication.created_at.asc())
        )
        rows = (await self._session.execute(stmt)).all()
        return [(application, project_name) for application, project_name in rows]

    async def save(self, application: TaskApplication) -> TaskApplication:
        await self._session.flush()
        await self._session.refresh(application)
        return application


class TaskRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def add(
        self, *, template_id: uuid.UUID, title: str, description: str
    ) -> Task:
        task = Task(template_id=template_id, title=title, description=description)
        self._session.add(task)
        await self._session.flush()
        await self._session.refresh(task)
        return task

    async def get(self, task_id: uuid.UUID) -> Task | None:
        return await self._session.get(Task, task_id)

    async def list_for_template(self, template_id: uuid.UUID) -> list[Task]:
        stmt = (
            select(Task)
            .where(Task.template_id == template_id)
            .order_by(Task.created_at.desc())
        )
        return list((await self._session.scalars(stmt)).all())

    async def count_for_template(self, template_id: uuid.UUID) -> int:
        stmt = (
            select(func.count())
            .select_from(Task)
            .where(Task.template_id == template_id)
        )
        return int((await self._session.scalar(stmt)) or 0)
