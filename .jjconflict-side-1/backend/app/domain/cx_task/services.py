"""Task Template & Task business logic (incl. 匹配市场, spec §13 阶段 6)."""

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, ValidationError
from app.domain.cx_notification.models import NotifKind, NotifLevel
from app.domain.cx_notification.services import NotificationService
from app.domain.cx_task.models import (
    ApplicationStatus,
    Task,
    TaskApplication,
    TaskTemplate,
)
from app.domain.cx_task.repositories import (
    TaskApplicationRepository,
    TaskRepository,
    TaskTemplateRepository,
)
from app.domain.project.repositories import ProjectRepository
from app.domain.project.services import ProjectService
from app.domain.space.repositories import SpaceRepository


class TaskTemplateService:
    def __init__(self, session: AsyncSession):
        self._session = session
        self._repo = TaskTemplateRepository(session)
        self._spaces = SpaceRepository(session)

    async def create(
        self,
        *,
        space_id: int,
        name: str,
        description: str,
        resource_pack: dict,
        conditions: list[dict],
        default_role: str | None,
    ) -> TaskTemplate:
        if await self._spaces.get_by_id(space_id) is None:
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

    async def list_for_space(self, space_id: int) -> tuple[list[TaskTemplate], int]:
        if await self._spaces.get_by_id(space_id) is None:
            raise NotFoundError("Space not found")
        return (
            await self._repo.list_for_space(space_id),
            await self._repo.count_for_space(space_id),
        )

    async def set_published(
        self, *, space_id: int, template_id: uuid.UUID, published: bool
    ) -> TaskTemplate:
        """(Un)list a template on the 匹配市场. Scoped to its owning Space."""
        template = await self.get_or_404(template_id)
        if template.space_id != space_id:
            raise NotFoundError("Task template not found in this space")
        template.published = published
        await self._session.flush()
        return template

    async def list_published(
        self, *, query: str | None = None
    ) -> list[tuple[TaskTemplate, str]]:
        """Market catalog: (template, space_name) pairs, keyword-filtered."""
        return await self._repo.list_published(query=query)


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


class TaskApplicationService:
    """应征 lifecycle on the 匹配市场 (spec §13 阶段 6).

    State machine: pending → accepted | declined (terminal). Re-applying and
    re-deciding the same way are idempotent; flipping a terminal decision is a
    validation error.
    """

    def __init__(self, session: AsyncSession):
        self._session = session
        self._repo = TaskApplicationRepository(session)
        self._templates = TaskTemplateRepository(session)
        self._tasks = TaskRepository(session)
        self._projects = ProjectRepository(session)
        self._project_service = ProjectService(session)
        self._notifications = NotificationService(session)

    async def apply(
        self, *, template_id: uuid.UUID, project_id: uuid.UUID, pitch: str
    ) -> TaskApplication:
        """A team applies with one of its projects. Idempotent per pair."""
        template = await self._templates.get(template_id)
        if template is None:
            raise NotFoundError("Task template not found")
        if not template.published:
            raise ValidationError("该题目未发布到市场，无法应征")
        if await self._projects.get(project_id) is None:
            raise NotFoundError("Project not found")
        existing = await self._repo.get_for(
            template_id=template_id, project_id=project_id
        )
        if existing is not None:
            return existing
        return await self._repo.add(
            template_id=template_id, project_id=project_id, pitch=pitch
        )

    async def get_or_404(self, application_id: uuid.UUID) -> TaskApplication:
        application = await self._repo.get(application_id)
        if application is None:
            raise NotFoundError("Application not found")
        return application

    async def list_for_template(
        self, template_id: uuid.UUID
    ) -> list[tuple[TaskApplication, str]]:
        if await self._templates.get(template_id) is None:
            raise NotFoundError("Task template not found")
        return await self._repo.list_for_template(template_id)

    async def accept(
        self, *, application_id: uuid.UUID, decided_by: str | None
    ) -> TaskApplication:
        """Accept = sign the protocol: create a Task under the template, link
        the project to it (reuses ProjectService.link_task, §4.2), notify the
        project. Accepting an accepted application is a no-op."""
        application = await self.get_or_404(application_id)
        if application.status == ApplicationStatus.accepted:
            return application
        if application.status == ApplicationStatus.declined:
            raise ValidationError("该应征已被婉拒，不能再接受")
        template = await self._templates.get(application.template_id)
        if template is None:
            raise NotFoundError("Task template not found")
        project = await self._projects.get(application.project_id)
        if project is None:
            raise NotFoundError("Project not found")
        # The concrete 题目 this team takes on: named after the applying
        # project (both titles are human-authored data, no NL extraction).
        task = await self._tasks.add(
            template_id=template.id,
            title=project.name,
            description=application.pitch,
        )
        await self._project_service.link_task(project_id=project.id, task_id=task.id)
        application.status = ApplicationStatus.accepted
        application.decided_by = decided_by
        application.decided_at = datetime.now(UTC)
        application.task_id = task.id
        await self._notifications.create(
            project_id=project.id,
            level=NotifLevel.light,
            kind=NotifKind.change_alert,
            title=f"应征已通过：「{template.name}」",
            body="项目已链接到该题目，资源包与条件即刻生效。",
            payload={
                "application_id": str(application.id),
                "template_id": str(template.id),
                "task_id": str(task.id),
            },
        )
        return await self._repo.save(application)

    async def decline(
        self, *, application_id: uuid.UUID, decided_by: str | None
    ) -> TaskApplication:
        """Decline an application. Declining a declined one is a no-op."""
        application = await self.get_or_404(application_id)
        if application.status == ApplicationStatus.declined:
            return application
        if application.status == ApplicationStatus.accepted:
            raise ValidationError("该应征已被接受，不能再婉拒")
        template = await self._templates.get(application.template_id)
        application.status = ApplicationStatus.declined
        application.decided_by = decided_by
        application.decided_at = datetime.now(UTC)
        await self._notifications.create(
            project_id=application.project_id,
            level=NotifLevel.light,
            kind=NotifKind.change_alert,
            title=f"应征未通过：「{template.name if template else ''}」",
            body="这次没有匹配上，可以继续在市场里寻找其他题目。",
            payload={
                "application_id": str(application.id),
                "template_id": str(application.template_id),
            },
        )
        return await self._repo.save(application)
