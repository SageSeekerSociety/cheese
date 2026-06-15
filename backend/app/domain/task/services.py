"""Task Template & Task business logic."""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.domain.space.repositories import SpaceRepository
from app.domain.task.models import Task, TaskTemplate
from app.domain.task.repositories import TaskRepository, TaskTemplateRepository


class TaskTemplateService:
    def __init__(self, session: AsyncSession):
        self._repo = TaskTemplateRepository(session)
        self._spaces = SpaceRepository(session)

    async def create(
        self,
        *,
        space_id: uuid.UUID,
        name: str,
        description: str,
        resource_pack: dict,
        conditions: list[dict],
        default_role: str | None,
    ) -> TaskTemplate:
        if await self._spaces.get(space_id) is None:
            raise NotFoundError("Space not found")
        return await self._repo.add(
            space_id=space_id,
            name=name,
            description=description,
            resource_pack=resource_pack,
            conditions=conditions,
            default_role=default_role,
        )

    async def get_or_404(self, template_id: uuid.UUID) -> TaskTemplate:
        template = await self._repo.get(template_id)
        if template is None:
            raise NotFoundError("Task template not found")
        return template

    async def list_for_space(
        self, space_id: uuid.UUID
    ) -> tuple[list[TaskTemplate], int]:
        if await self._spaces.get(space_id) is None:
            raise NotFoundError("Space not found")
        return (
            await self._repo.list_for_space(space_id),
            await self._repo.count_for_space(space_id),
        )


class TaskService:
    def __init__(self, session: AsyncSession):
        self._repo = TaskRepository(session)
        self._templates = TaskTemplateRepository(session)

    async def create(
        self, *, template_id: uuid.UUID, title: str, description: str
    ) -> Task:
        if await self._templates.get(template_id) is None:
            raise NotFoundError("Task template not found")
        return await self._repo.add(
            template_id=template_id, title=title, description=description
        )

    async def get_or_404(self, task_id: uuid.UUID) -> Task:
        task = await self._repo.get(task_id)
        if task is None:
            raise NotFoundError("Task not found")
        return task

    async def list_for_template(self, template_id: uuid.UUID) -> tuple[list[Task], int]:
        if await self._templates.get(template_id) is None:
            raise NotFoundError("Task template not found")
        return (
            await self._repo.list_for_template(template_id),
            await self._repo.count_for_template(template_id),
        )
