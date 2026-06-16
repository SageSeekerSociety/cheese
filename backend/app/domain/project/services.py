"""Project business logic."""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, ValidationError
from app.domain.project.models import AiMode, Project, ProjectTaskLink
from app.domain.project.repositories import ProjectRepository
from app.domain.task.repositories import TaskRepository, TaskTemplateRepository
from app.domain.topic.models import TopicKind
from app.domain.topic.repositories import TopicRepository


class ProjectService:
    def __init__(self, session: AsyncSession):
        self._repo = ProjectRepository(session)
        self._topics = TopicRepository(session)
        self._tasks = TaskRepository(session)
        self._templates = TaskTemplateRepository(session)

    async def create(
        self,
        *,
        name: str,
        owner_handle: str | None = None,
        ai_mode: AiMode = AiMode.collaborative,
        expert_role: str | None = None,
    ) -> Project:
        """Create a project and its root topic (= 项目本身, spec §6)."""
        project = await self._repo.add(
            name=name,
            owner_handle=owner_handle,
            ai_mode=ai_mode,
            expert_role=expert_role,
        )
        root = await self._topics.add(
            project_id=project.id,
            title=f"{name} · 项目总览",
            kind=TopicKind.root,
            created_by=owner_handle,
        )
        await self._repo.set_root_topic(project, root.id)
        return project

    async def get_or_404(self, project_id: uuid.UUID) -> Project:
        project = await self._repo.get(project_id)
        if project is None:
            raise NotFoundError("Project not found")
        return project

    async def list_all(self) -> tuple[list[Project], int]:
        return await self._repo.list_all(), await self._repo.count()

    async def link_task(
        self, *, project_id: uuid.UUID, task_id: uuid.UUID
    ) -> ProjectTaskLink:
        """Link a project to a task = accept the Template's protocol (§4.2)."""
        project = await self.get_or_404(project_id)
        task = await self._tasks.get(task_id)
        if task is None:
            raise NotFoundError("Task not found")
        if await self._repo.get_link(project_id=project_id, task_id=task_id):
            raise ValidationError("Project already linked to this task")
        # Inherit the Template's default expert role if the project has none yet
        # (§4.2: accepting the protocol也继承默认配置).
        if not project.expert_role:
            tmpl = await self._templates.get(task.template_id)
            if tmpl is not None and tmpl.default_role:
                project.expert_role = tmpl.default_role
        return await self._repo.link_task(project_id=project_id, task_id=task_id)

    async def unlink_task(
        self, *, project_id: uuid.UUID, task_id: uuid.UUID
    ) -> None:
        """退出/断开 Task 协议 (§4): remove the project↔task link."""
        await self.get_or_404(project_id)
        if not await self._repo.unlink_task(project_id=project_id, task_id=task_id):
            raise NotFoundError("Project is not linked to this task")

    async def list_links(
        self, project_id: uuid.UUID
    ) -> tuple[list[ProjectTaskLink], int]:
        await self.get_or_404(project_id)
        links = await self._repo.list_links(project_id)
        return links, len(links)
