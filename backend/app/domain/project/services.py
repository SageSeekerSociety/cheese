from collections.abc import Sequence
from datetime import UTC

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
        team_id: int | None,
        leader_id: int,
        start_date: int | None,
        end_date: int | None,
        content: str | None = None,
        parent_id: int | None = None,
        external_task_id: int | None = None,
        github_repo: str | None = None,
    ) -> Project:
        from datetime import datetime

        start_dt = datetime.fromtimestamp(start_date / 1000, tz=UTC) if start_date is not None else None
        end_dt = datetime.fromtimestamp(end_date / 1000, tz=UTC) if end_date is not None else None
        project = await self._repo.create_project(
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
        # The creator is a member (OWNER) of their own project, so membership-gated
        # access (documents, agents, 现场) works immediately — without this the leader
        # could not even open the project they just made.
        if self._membership_repo is not None:
            await self._membership_repo.add_member(
                project_id=project.id, user_id=leader_id, role=ProjectMemberRole.OWNER
            )
        return project

    async def create_personal_project(
        self, *, name: str, description: str, leader_id: int, color_code: str = "#5B8FF9"
    ) -> Project:
        """A 知是 2.0 independent project created from the workspace onboarding: owned by
        the creator, no team, no fixed schedule. The creator is added as OWNER."""
        return await self.create_project(
            name=name,
            description=description,
            color_code=color_code,
            team_id=None,
            leader_id=leader_id,
            start_date=None,
            end_date=None,
        )

    async def list_projects_for_member(self, user_id: int) -> list[Project]:
        """Every non-deleted project the user is a member of (includes teamless personal
        projects, which team-scoped listing would miss)."""
        repo = self._require_membership_repo()
        project_ids = await repo.list_project_ids_for_user(user_id)
        if not project_ids:
            return []
        by_id = await self._repo.get_by_ids(project_ids)
        return [by_id[pid] for pid in project_ids if pid in by_id and not by_id[pid].archived]

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
