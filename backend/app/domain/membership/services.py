"""Project membership business logic.

Every write here is authorized against the caller: the roster decides who can
reach the project's topics at all (``authorize_topic_access``), so ``actor`` is
a required argument, not an optional courtesy — a future caller that forgets to
pass one fails to compile rather than silently writing unauthorized.
"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ForbiddenError, NotFoundError, ValidationError
from app.domain.authz.policy import can_manage_project_members
from app.domain.identity.actor import Actor
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

    async def _require_manager(self, project_id: uuid.UUID, actor: Actor) -> None:
        """Only the project's owner or a lead (verified by token) may write the
        roster. Called AFTER ``_ensure_project`` so a missing project still reads
        as 404 rather than 403 for everyone."""

        async def owner_of(pid: uuid.UUID) -> str | None:
            project = await self._projects.get(pid)
            return project.owner_handle if project is not None else None

        async def role_of(pid: uuid.UUID, handle: str) -> ProjectRole | None:
            member = await self._repo.get(project_id=pid, user_handle=handle)
            return member.role if member is not None else None

        allowed = await can_manage_project_members(
            actor,
            project_id=project_id,
            project_owner=owner_of,
            project_role=role_of,
        )
        if not allowed:
            raise ForbiddenError("只有项目的 owner / lead 能管理项目成员")

    async def add(
        self,
        *,
        project_id: uuid.UUID,
        user_handle: str,
        role: ProjectRole,
        actor: Actor,
    ) -> ProjectMember:
        await self._ensure_project(project_id)
        await self._require_manager(project_id, actor)
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
        self,
        *,
        project_id: uuid.UUID,
        user_handle: str,
        role: ProjectRole,
        actor: Actor,
    ) -> ProjectMember:
        await self._ensure_project(project_id)
        await self._require_manager(project_id, actor)
        member = await self._repo.get(project_id=project_id, user_handle=user_handle)
        if member is None:
            raise NotFoundError("Member not found")
        return await self._repo.update_role(member, role=role)

    async def remove(
        self, *, project_id: uuid.UUID, user_handle: str, actor: Actor
    ) -> None:
        await self._ensure_project(project_id)
        await self._require_manager(project_id, actor)
        member = await self._repo.get(project_id=project_id, user_handle=user_handle)
        if member is None:
            raise NotFoundError("Member not found")
        await self._repo.delete(member)
