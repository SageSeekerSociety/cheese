from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.team_project.models import Project, ProjectMembership


class ProjectRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, project_id: int) -> Project | None:
        stmt = select(Project).where(
            Project.id == project_id, Project.deleted_at.is_(None)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def list_by_team(
        self,
        *,
        team_id: int,
        parent_id: int | None = None,
        leader_id: int | None = None,
        member_id: int | None = None,
        archived: bool | None = None,
    ) -> Sequence[Project]:
        stmt = (
            select(Project)
            .where(Project.team_id == team_id, Project.deleted_at.is_(None))
            .order_by(Project.created_at.asc())
        )
        if parent_id is not None:
            stmt = stmt.where(Project.parent_id == parent_id)
        if leader_id is not None:
            stmt = stmt.where(Project.leader_id == leader_id)
        if archived is not None:
            stmt = stmt.where(Project.archived.is_(archived))
        if member_id is not None:
            member_projects = select(ProjectMembership.project_id).where(
                ProjectMembership.user_id == member_id,
                ProjectMembership.deleted_at.is_(None),
            )
            stmt = stmt.where(Project.id.in_(member_projects))
        return (await self._session.execute(stmt)).scalars().all()

    async def create(self, project: Project) -> Project:
        now = datetime.now(UTC)
        project.created_at = now
        project.updated_at = now
        self._session.add(project)
        await self._session.flush()
        return project

    async def touch(self, project: Project) -> None:
        project.updated_at = datetime.now(UTC)
        await self._session.flush()

    async def soft_delete(self, project: Project) -> None:
        project.deleted_at = datetime.now(UTC)
        await self._session.flush()


class ProjectMembershipRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_by_project(self, project_id: int) -> Sequence[ProjectMembership]:
        stmt = (
            select(ProjectMembership)
            .where(
                ProjectMembership.project_id == project_id,
                ProjectMembership.deleted_at.is_(None),
            )
            .order_by(ProjectMembership.created_at.asc())
        )
        return (await self._session.execute(stmt)).scalars().all()

    async def list_by_projects(
        self, project_ids: Sequence[int]
    ) -> Sequence[ProjectMembership]:
        if not project_ids:
            return []
        stmt = select(ProjectMembership).where(
            ProjectMembership.project_id.in_(list(project_ids)),
            ProjectMembership.deleted_at.is_(None),
        )
        return (await self._session.execute(stmt)).scalars().all()

    async def get(self, project_id: int, user_id: int) -> ProjectMembership | None:
        stmt = select(ProjectMembership).where(
            ProjectMembership.project_id == project_id,
            ProjectMembership.user_id == user_id,
            ProjectMembership.deleted_at.is_(None),
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def add(
        self, *, project_id: int, user_id: int, role: str, notes: str | None = None
    ) -> ProjectMembership:
        now = datetime.now(UTC)
        membership = ProjectMembership(
            project_id=project_id,
            user_id=user_id,
            role=role,
            notes=notes,
            created_at=now,
            updated_at=now,
        )
        self._session.add(membership)
        await self._session.flush()
        return membership

    async def update_role(
        self, membership: ProjectMembership, role: str, notes: str | None = None
    ) -> None:
        membership.role = role
        if notes is not None:
            membership.notes = notes
        membership.updated_at = datetime.now(UTC)
        await self._session.flush()

    async def soft_delete(self, membership: ProjectMembership) -> None:
        membership.deleted_at = datetime.now(UTC)
        await self._session.flush()
