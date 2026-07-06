"""Shared helpers for the 2.0 routes."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ForbiddenError, NotFoundError
from app.domain.project.repositories import ProjectMembershipRepository, ProjectRepository


async def require_project_access(db: AsyncSession, user_id: int, project_id: int) -> None:
    """Raise unless the user is a member or the leader of the project.

    The defensible baseline for accessing a project's 2.0 conversation/document
    substrate. Finer per-thread authz (ThreadMembership) is a future refinement.
    """
    project = await ProjectRepository(db).get_by_id(project_id)
    if project is None:
        raise NotFoundError(f"Project {project_id} not found")
    if project.leader_id == user_id:
        return
    if await ProjectMembershipRepository(db).get_relation(project_id, user_id) is None:
        raise ForbiddenError("not a member of this project")
