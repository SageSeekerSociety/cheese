from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import Select, and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.project.models import Project, ProjectMemberRole, ProjectMembership


class ProjectRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_project(
        self,
        *,
        name: str,
        description: str,
        color_code: str,
        team_id: int,
        leader_id: int,
        start_date: datetime,
        end_date: datetime,
        content: str | None = None,
        parent_id: int | None = None,
        external_task_id: int | None = None,
        github_repo: str | None = None,
    ) -> Project:
        now = datetime.now(UTC)
        project = Project(
            name=name,
            description=description,
            color_code=color_code,
            content=content or "",
            team_id=team_id,
            leader_id=leader_id,
            parent_id=parent_id,
            external_task_id=external_task_id,
            github_repo=github_repo,
            start_date=start_date,
            end_date=end_date,
            archived=False,
            created_at=now,
            updated_at=now,
            deleted_at=None,
        )
        self._session.add(project)
        await self._session.flush()
        return project

    async def get_by_ids(self, ids: Sequence[int]) -> dict[int, Project]:
        if not ids:
            return {}
        stmt: Select[tuple[Project]] = select(Project).where(
            and_(Project.id.in_(list(ids)), Project.deleted_at.is_(None))
        )
        result = await self._session.execute(stmt)
        rows = list(result.scalars().all())
        return {row.id: row for row in rows}

    async def get_by_id(self, project_id: int) -> Project | None:
        stmt: Select[tuple[Project]] = select(Project).where(
            and_(Project.id == project_id, Project.deleted_at.is_(None))
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def save(self, project: Project) -> Project:
        project.updated_at = datetime.now(UTC)
        await self._session.flush()
        return project

    async def soft_delete(self, project: Project) -> None:
        project.deleted_at = datetime.now(UTC)
        await self._session.flush()

    async def list_projects(
        self,
        *,
        team_id: int,
        parent_id: int | None = None,
        leader_id: int | None = None,
        member_id: int | None = None,
        archived: bool | None = None,
    ) -> Sequence[Project]:
        stmt: Select[tuple[Project]] = select(Project).where(
            Project.deleted_at.is_(None),
            Project.team_id == team_id,
        )
        if parent_id is not None:
            stmt = stmt.where(Project.parent_id == parent_id)
        if leader_id is not None:
            stmt = stmt.where(Project.leader_id == leader_id)
        if member_id is not None:
            stmt = stmt.where(
                Project.id.in_(
                    select(ProjectMembership.project_id).where(
                        ProjectMembership.user_id == member_id,
                        ProjectMembership.deleted_at.is_(None),
                    )
                )
            )
        if archived is not None:
            stmt = stmt.where(Project.archived.is_(archived))
        stmt = stmt.order_by(Project.id.asc())
        result = await self._session.execute(stmt)
        return list(result.scalars().all())


class ProjectMembershipRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_member(
        self,
        *,
        project_id: int,
        user_id: int,
        role: ProjectMemberRole,
        notes: str = "",
    ) -> ProjectMembership:
        now = datetime.now(UTC)
        membership = ProjectMembership(
            project_id=project_id,
            user_id=user_id,
            role=role.value,
            notes=notes,
            created_at=now,
            updated_at=now,
            deleted_at=None,
        )
        self._session.add(membership)
        await self._session.flush()
        return membership

    async def get_relation(self, project_id: int, user_id: int) -> ProjectMembership | None:
        stmt: Select[tuple[ProjectMembership]] = select(ProjectMembership).where(
            ProjectMembership.project_id == project_id,
            ProjectMembership.user_id == user_id,
            ProjectMembership.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_members(
        self,
        project_id: int,
        *,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[ProjectMembership], int]:
        base = and_(
            ProjectMembership.project_id == project_id,
            ProjectMembership.deleted_at.is_(None),
        )
        count_stmt = select(func.count(ProjectMembership.id)).where(base)
        count_result = await self._session.execute(count_stmt)
        total = int(count_result.scalar_one() or 0)

        stmt: Select[tuple[ProjectMembership]] = (
            select(ProjectMembership)
            .where(base)
            .order_by(ProjectMembership.created_at.asc())
            .limit(limit)
            .offset(offset)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all()), total

    async def remove_member(self, membership: ProjectMembership) -> None:
        now = datetime.now(UTC)
        membership.deleted_at = now
        membership.updated_at = now
        await self._session.flush()
