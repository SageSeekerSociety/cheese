"""Task Template & Task data access."""

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.task.models import Task, TaskTemplate


class TaskTemplateRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def add(
        self,
        *,
        space_id: uuid.UUID,
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

    async def list_for_space(self, space_id: uuid.UUID) -> list[TaskTemplate]:
        stmt = (
            select(TaskTemplate)
            .where(TaskTemplate.space_id == space_id)
            .order_by(TaskTemplate.created_at.desc())
        )
        return list((await self._session.scalars(stmt)).all())

    async def count_for_space(self, space_id: uuid.UUID) -> int:
        stmt = (
            select(func.count())
            .select_from(TaskTemplate)
            .where(TaskTemplate.space_id == space_id)
        )
        return int((await self._session.scalar(stmt)) or 0)


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
