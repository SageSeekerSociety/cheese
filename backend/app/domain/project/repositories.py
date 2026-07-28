"""Project data access."""

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.project.models import (
    AiMode,
    Project,
    ProjectMember,
    ProjectTaskLink,
)
from app.domain.user.models import User, UserProfile


class ProjectRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def add(
        self,
        *,
        name: str,
        owner_handle: str | None = None,
        ai_mode: AiMode = AiMode.collaborative,
        expert_role: str | None = None,
        team_id: int | None = None,
        external_task_id: int | None = None,
    ) -> Project:
        project = Project(
            name=name,
            owner_handle=owner_handle,
            ai_mode=ai_mode,
            expert_role=expert_role,
            team_id=team_id,
            external_task_id=external_task_id,
        )
        self._session.add(project)
        await self._session.flush()
        await self._session.refresh(project)
        return project

    async def get(self, project_id: uuid.UUID) -> Project | None:
        return await self._session.get(Project, project_id)

    async def get_by_team(self, team_id: int) -> Project | None:
        """The AI-workspace project for a 知是 Team (P4 native link), newest first."""
        stmt = (
            select(Project)
            .where(Project.team_id == team_id)
            .order_by(Project.created_at.desc())
            .limit(1)
        )
        return (await self._session.scalars(stmt)).first()

    async def list_all(self) -> list[Project]:
        stmt = select(Project).order_by(Project.created_at.desc())
        return list((await self._session.scalars(stmt)).all())

    async def list_by_team(self, team_id: int) -> list[Project]:
        """All projects owned by a team (项目归团队), newest first."""
        stmt = (
            select(Project)
            .where(Project.team_id == team_id)
            .order_by(Project.created_at.desc())
        )
        return list((await self._session.scalars(stmt)).all())

    async def list_personal_legacy(self, owner_handle: str) -> list[Project]:
        """Pre-personal-team rows: team-less projects of one owner. New personal
        projects carry the personal team's id; these are the NULL-team leftovers
        that must still show on the personal team's 项目 page."""
        stmt = (
            select(Project)
            .where(Project.team_id.is_(None), Project.owner_handle == owner_handle)
            .order_by(Project.created_at.desc())
        )
        return list((await self._session.scalars(stmt)).all())

    async def count(self) -> int:
        return int(
            (await self._session.scalar(select(func.count()).select_from(Project))) or 0
        )

    async def set_root_topic(self, project: Project, root_topic_id: uuid.UUID) -> None:
        project.root_topic_id = root_topic_id
        await self._session.flush()

    async def set_summary(self, project: Project, summary: str) -> None:
        project.summary = summary
        await self._session.flush()

    async def link_task(
        self, *, project_id: uuid.UUID, task_id: uuid.UUID
    ) -> ProjectTaskLink:
        link = ProjectTaskLink(project_id=project_id, task_id=task_id)
        self._session.add(link)
        await self._session.flush()
        await self._session.refresh(link)
        return link

    async def unlink_task(self, *, project_id: uuid.UUID, task_id: uuid.UUID) -> bool:
        link = await self.get_link(project_id=project_id, task_id=task_id)
        if link is None:
            return False
        await self._session.delete(link)
        await self._session.flush()
        return True

    async def get_link(
        self, *, project_id: uuid.UUID, task_id: uuid.UUID
    ) -> ProjectTaskLink | None:
        stmt = select(ProjectTaskLink).where(
            ProjectTaskLink.project_id == project_id,
            ProjectTaskLink.task_id == task_id,
        )
        return (await self._session.scalars(stmt)).first()

    async def list_links(self, project_id: uuid.UUID) -> list[ProjectTaskLink]:
        stmt = select(ProjectTaskLink).where(ProjectTaskLink.project_id == project_id)
        return list((await self._session.scalars(stmt)).all())

    async def list_members(self, project_id: uuid.UUID) -> list[dict]:
        """Project roster: each member's handle, display name, and role — used to
        inject 芝士's teammate context and to resolve @mentions to a handle."""
        # Display name lives on UserProfile.nickname (main's User has only
        # username); join both, keyed by handle == username (fusion identity).
        stmt = (
            select(ProjectMember.user_handle, ProjectMember.role, UserProfile.nickname)
            .join(User, User.username == ProjectMember.user_handle, isouter=True)
            .join(UserProfile, UserProfile.user_id == User.id, isouter=True)
            .where(ProjectMember.project_id == project_id)
        )
        rows = (await self._session.execute(stmt)).all()
        return [
            {"handle": h, "role": str(role), "name": name or h}
            for (h, role, name) in rows
        ]

    async def list_projects_for_task(self, task_id: uuid.UUID) -> list[ProjectTaskLink]:
        stmt = select(ProjectTaskLink).where(ProjectTaskLink.task_id == task_id)
        return list((await self._session.scalars(stmt)).all())

    async def list_for_external_task(self, task_id: int) -> list[Project]:
        """Every project created from this 赛题 — the way back the link exists for."""
        result = await self._session.execute(
            select(Project)
            .where(Project.external_task_id == task_id)
            .order_by(Project.created_at)
        )
        return list(result.scalars())
