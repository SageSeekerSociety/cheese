"""Same-session project facts without management or transaction ownership."""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.core.sentences import say
from app.domain.project.models import Project
from app.domain.project.repositories import ProjectRepository


async def load_project(session: AsyncSession, project_id: uuid.UUID) -> Project | None:
    """Read the caller's project row, preserving identity-map and autoflush rules."""
    return await ProjectRepository(session).get(project_id)


async def project_or_404(repo: ProjectRepository, project_id: uuid.UUID) -> Project:
    """The project, or the catalog's ``projectNotFound`` refusal.

    Every service that needs a project row for authorization or for its own
    work reads it through here. A private copy of these three lines is how four
    of them drifted apart, so the guard in ``tests/unit/test_project_reads.py``
    refuses a new one.
    """
    project = await repo.get(project_id)
    if project is None:
        raise NotFoundError(say("projectNotFound"))
    return project
