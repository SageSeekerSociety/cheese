"""Which projects wait for their deploy before a finished task closes.

A task's last delivery merging is not the change being live: the project's
default branch runs its CI and deploys only when that is green
(`.github/workflows/deploy-dev.yml`). A project whose deploy tells this
platform what it put live (`deploy_wait.ship`) keeps such a task open, waiting,
until a deploy that contains the merge has reported (`review.task_landing`).
Every other project has no "live" to wait for, and its tasks are written up
and closed on merge.

A project waits when its repository is one of
`settings.task_deploy_wait_repositories` and a deploy of that repository has
reported here within `REPORTS_FRESH`. The second half keeps an instance that
no deploy reports to — another installation, a deploy whose report step is
broken for weeks — from leaving tasks waiting for a report that is not coming.
"""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.domain.project.models import ProjectForge, ProjectGitInstallation
from app.domain.review.models import DeployReport

#: How recent the last deploy report must be for a project to wait for its
#: deploy. Main red for longer than this stops new merges from waiting; the
#: tasks already waiting keep waiting.
REPORTS_FRESH = timedelta(days=14)


def deploy_repositories() -> set[str]:
    """The repositories whose deploy reports here, lower case "owner/repo"."""
    return {
        r.strip().lower() for r in settings.task_deploy_wait_repositories if r.strip()
    }


def repository_of_url(url: str) -> str:
    """ "owner/repo" of a forge URL such as https://github.com/OWNER/REPO, lower
    case; the input as given, lowered, when it is already that."""
    path = url.strip().rstrip("/")
    if path.endswith(".git"):
        path = path[: -len(".git")]
    return "/".join(path.split("/")[-2:]).lower()


async def project_repositories(
    session: AsyncSession, project_id: uuid.UUID
) -> set[str]:
    """The repositories ``project_id`` works on, lower case."""
    found: set[str] = set()
    for model in (ProjectForge, ProjectGitInstallation):
        found.update(
            await session.scalars(
                select(func.lower(func.trim(model.repo))).where(
                    model.project_id == project_id
                )
            )
        )
    return found


async def projects_of(session: AsyncSession, repo: str) -> set[uuid.UUID]:
    """The projects working on ``repo`` ("owner/repo", any case)."""
    found: set[uuid.UUID] = set()
    for model in (ProjectForge, ProjectGitInstallation):
        found.update(
            await session.scalars(
                select(model.project_id).where(
                    func.lower(func.trim(model.repo)) == repo.strip().lower()
                )
            )
        )
    return found


async def waits_for_deploy(session: AsyncSession, project_id: uuid.UUID) -> bool:
    """Whether a finished task in ``project_id`` waits for its deploy (module
    docstring)."""
    repos = (await project_repositories(session, project_id)) & deploy_repositories()
    if not repos:
        return False
    since = datetime.now(UTC) - REPORTS_FRESH
    return (
        await session.scalar(
            select(DeployReport.id)
            .where(DeployReport.repo.in_(repos), DeployReport.reported_at >= since)
            .limit(1)
        )
    ) is not None
