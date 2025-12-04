from __future__ import annotations

from collections.abc import Sequence

from app.domain.project.models import Project
from app.domain.project.repositories import ProjectRepository


class ProjectService:
    def __init__(self, repo: ProjectRepository) -> None:
        self._repo = repo

    async def create_project(
        self,
        *,
        name: str,
        description: str,
        color_code: str,
    ) -> Project:
        return await self._repo.create_project(
            name=name,
            description=description,
            color_code=color_code,
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
