"""Same-session project facts without management or transaction ownership."""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.project.models import Project
from app.domain.project.repositories import ProjectRepository


async def load_project(session: AsyncSession, project_id: uuid.UUID) -> Project | None:
    """Read the caller's project row, preserving identity-map and autoflush rules."""
    return await ProjectRepository(session).get(project_id)
