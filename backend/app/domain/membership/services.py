"""Project membership business logic."""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, ValidationError
from app.domain.membership.repositories import MemberRepository
from app.domain.project.models import ProjectMember, ProjectRole
from app.domain.project.repositories import ProjectRepository


class MemberService:
    def __init__(self, session: AsyncSession):
        self._repo = MemberRepository(session)
        self._projects = ProjectRepository(session)

    async def _ensure_project(self, project_id: uuid.UUID) -> None:
        if await self._projects.get(project_id) is None:
            raise NotFoundError("Project not found")

    async def add(
        self, *, project_id: uuid.UUID, user_handle: str, role: ProjectRole
    ) -> ProjectMember:
        await self._ensure_project(project_id)
        existing = await self._repo.get(project_id=project_id, user_handle=user_handle)
        if existing is not None:
            raise ValidationError("User is already a member of this project")
        return await self._repo.add(
            project_id=project_id, user_handle=user_handle, role=role
        )

    async def list_for_project(
        self, project_id: uuid.UUID
    ) -> tuple[list[ProjectMember], int]:
        await self._ensure_project(project_id)
        return (
            await self._repo.list_for_project(project_id),
            await self._repo.count_for_project(project_id),
        )

    async def update_role(
        self, *, project_id: uuid.UUID, user_handle: str, role: ProjectRole
    ) -> ProjectMember:
        await self._ensure_project(project_id)
        member = await self._repo.get(project_id=project_id, user_handle=user_handle)
        if member is None:
            raise NotFoundError("Member not found")
        return await self._repo.update_role(member, role=role)

    async def remove(self, *, project_id: uuid.UUID, user_handle: str) -> None:
        await self._ensure_project(project_id)
        member = await self._repo.get(project_id=project_id, user_handle=user_handle)
        if member is None:
            raise NotFoundError("Member not found")
        await self._repo.delete(member)
