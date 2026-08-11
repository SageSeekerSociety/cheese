"""Project membership data access."""

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.project.models import ProjectMember, ProjectRole


class MemberRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def add(
        self, *, project_id: uuid.UUID, user_handle: str, role: ProjectRole
    ) -> ProjectMember:
        member = ProjectMember(
            project_id=project_id, user_handle=user_handle, role=role
        )
        self._session.add(member)
        await self._session.flush()
        await self._session.refresh(member)
        return member

    async def get(
        self, *, project_id: uuid.UUID, user_handle: str
    ) -> ProjectMember | None:
        stmt = select(ProjectMember).where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_handle == user_handle,
        )
        return await self._session.scalar(stmt)

    async def list_for_project(self, project_id: uuid.UUID) -> list[ProjectMember]:
        stmt = (
            select(ProjectMember)
            .where(ProjectMember.project_id == project_id)
            .order_by(ProjectMember.created_at.asc())
        )
        return list((await self._session.scalars(stmt)).all())

    async def count_for_project(self, project_id: uuid.UUID) -> int:
        stmt = (
            select(func.count())
            .select_from(ProjectMember)
            .where(ProjectMember.project_id == project_id)
        )
        return int((await self._session.scalar(stmt)) or 0)

    async def update_role(
        self, member: ProjectMember, *, role: ProjectRole
    ) -> ProjectMember:
        member.role = role
        await self._session.flush()
        await self._session.refresh(member)
        return member

    async def delete(self, member: ProjectMember) -> None:
        await self._session.delete(member)
        await self._session.flush()
