from __future__ import annotations

from collections.abc import Sequence

from app.core.errors import BadRequestError, NotFoundError
from app.domain.project.models import Project, ProjectMemberRole, ProjectMembership
from app.domain.project.repositories import ProjectMembershipRepository, ProjectRepository


class ProjectService:
    def __init__(
        self,
        repo: ProjectRepository,
        membership_repo: ProjectMembershipRepository | None = None,
    ) -> None:
        self._repo = repo
        self._membership_repo = membership_repo

    async def create_project(
        self,
        *,
        name: str,
        description: str,
        color_code: str,
        team_id: int,
        leader_id: int,
        start_date: int,
        end_date: int,
        content: str | None = None,
        parent_id: int | None = None,
        external_task_id: int | None = None,
        github_repo: str | None = None,
    ) -> Project:
        from datetime import datetime, timezone

        start_dt = datetime.fromtimestamp(start_date / 1000, tz=timezone.utc)
        end_dt = datetime.fromtimestamp(end_date / 1000, tz=timezone.utc)
        return await self._repo.create_project(
            name=name,
            description=description,
            color_code=color_code,
            team_id=team_id,
            leader_id=leader_id,
            start_date=start_dt,
            end_date=end_dt,
            content=content,
            parent_id=parent_id,
            external_task_id=external_task_id,
            github_repo=github_repo,
        )

    async def get_projects_by_ids(self, ids: Sequence[int]) -> dict[int, Project]:
        return await self._repo.get_by_ids(ids)

    async def get_project(self, project_id: int) -> Project | None:
        return await self._repo.get_by_id(project_id)

    async def update_project(
        self,
        project: Project,
        *,
        name: str | None = None,
        description: str | None = None,
        color_code: str | None = None,
        archived: bool | None = None,
    ) -> Project:
        if name is not None:
            project.name = name
        if description is not None:
            project.description = description
        if color_code is not None:
            project.color_code = color_code
        if archived is not None:
            project.archived = archived
        return await self._repo.save(project)

    async def soft_delete_project(self, project: Project) -> None:
        await self._repo.soft_delete(project)

    async def list_projects(
        self,
        *,
        team_id: int,
        parent_id: int | None = None,
        leader_id: int | None = None,
        member_id: int | None = None,
        archived: bool | None = None,
    ) -> Sequence[Project]:
        return await self._repo.list_projects(
            team_id=team_id,
            parent_id=parent_id,
            leader_id=leader_id,
            member_id=member_id,
            archived=archived,
        )

    # ------------------------------------------------------------------
    # Project membership
    # ------------------------------------------------------------------

    def _require_membership_repo(self) -> ProjectMembershipRepository:
        if self._membership_repo is None:
            raise BadRequestError("Project membership repository unavailable")
        return self._membership_repo

    async def list_members(
        self,
        project_id: int,
        *,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[ProjectMembership], int]:
        repo = self._require_membership_repo()
        return await repo.list_members(project_id, limit=limit, offset=offset)

    async def add_member(
        self,
        *,
        project_id: int,
        user_id: int,
        role: str,
        notes: str = "",
    ) -> ProjectMembership:
        repo = self._require_membership_repo()
        existing = await repo.get_relation(project_id, user_id)
        if existing is not None:
            raise BadRequestError("User is already a member of this project")
        role_enum = self._parse_role(role)
        return await repo.add_member(
            project_id=project_id,
            user_id=user_id,
            role=role_enum,
            notes=notes,
        )

    async def remove_member(
        self,
        *,
        project_id: int,
        user_id: int,
    ) -> None:
        repo = self._require_membership_repo()
        existing = await repo.get_relation(project_id, user_id)
        if existing is None:
            raise NotFoundError("Project membership not found")
        await repo.remove_member(existing)

    @staticmethod
    def _parse_role(role: str) -> ProjectMemberRole:
        mapping = {
            "MEMBER": ProjectMemberRole.MEMBER,
            "ADMIN": ProjectMemberRole.ADMIN,
            "OWNER": ProjectMemberRole.OWNER,
        }
        result = mapping.get(role.upper())
        if result is None:
            raise BadRequestError(f"Invalid role: {role}. Must be MEMBER, ADMIN, or OWNER")
        return result
