"""Move a platform-hosted project to a GitHub repository its people own.

A project made on the platform's Forgejo (every project a 题目 claim opens is
one) is switched to GitHub in three steps, each refusing before anything is
changed when it cannot go through:

1. What would point at the old repository afterwards must be settled first: a
   card waiting for review names a Forgejo PR, which GitHub cannot merge; a
   题目's starter code still being pushed would land on the repository being
   left.
2. Every branch and tag is copied to the GitHub repository, which must be
   empty (`github_migration.mirror_to_github`). The Forgejo repository is left
   as it is.
3. The binding switches to GitHub, and open tasks forget their Forgejo PRs so
   the draft sweep opens them again on GitHub. Closed tasks keep their links.

The copy runs with no transaction open: it can take minutes, and nothing it
reads is locked. Step 1 is asked again after it, since a card may have been
filed meanwhile; a refused switch leaves a GitHub repository holding the same
refs, which a later attempt accepts.
"""

import asyncio
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, GatewayUnavailableError
from app.core.sentences import say
from app.domain.project.forge import binding_for_project, starter_push, starter_remote
from app.domain.project.github_app import github_app_tokens_for_repo
from app.domain.project.github_migration import mirror_to_github
from app.domain.project.models import StarterPushState
from app.domain.project.repositories import (
    ProjectGitInstallationRepository,
    RepositoryTakenError,
)
from app.domain.project.services import ProjectService
from app.domain.room_task.services import TaskService


async def refuse_unless_movable(db: AsyncSession, project_id: uuid.UUID) -> None:
    """Refuse a project that is not hosted on Forgejo, or whose cards or
    starter code still point at the Forgejo repository."""
    binding = await binding_for_project(project_id, db)
    if binding is None or binding.kind != "forgejo":
        raise ConflictError(say("githubMigrationNotHosted"))
    # deferred-import: review sits above project; only this move needs it.
    from app.domain.review.services import AcceptService

    waiting = await AcceptService(db).open_pr_card_ids(project_id)
    if waiting:
        raise ConflictError(say("githubMigrationCardsPending", count=len(waiting)))
    starter = await starter_push(project_id, db)
    if starter is not None and starter.state == StarterPushState.pending:
        raise ConflictError(say("githubMigrationStarterPending"))


async def move_to_github(
    db: AsyncSession, project_id: uuid.UUID, installation_id: int, repo: dict
) -> dict:
    """Copy the project's Forgejo repository into ``repo`` (a repository of
    ``installation_id`` the caller may write, as GitHub listed it) and bind the
    project to it."""
    full_name = repo["full_name"]
    await refuse_unless_movable(db, project_id)
    installations = ProjectGitInstallationRepository(db)
    holder = await installations.get_by_repo(full_name)
    if holder is not None and holder.project_id != project_id:
        raise RepositoryTakenError(full_name, holder.project_id)
    minter = github_app_tokens_for_repo(
        installation_id, repository_id=repo.get("id"), repository=full_name
    )
    if minter is None:
        raise GatewayUnavailableError(say("githubRepoConnectUnavailable"))
    token, _ = await minter.write_token()
    source = await starter_remote(project_id, db)
    await db.commit()
    await asyncio.to_thread(
        mirror_to_github, source, f"https://github.com/{full_name}.git", token
    )
    await refuse_unless_movable(db, project_id)
    await installations.upsert(
        project_id=project_id,
        installation_id=installation_id,
        repo=full_name,
        repository_id=repo.get("id"),
        account=repo["owner"]["login"],
        moved_from_forgejo=True,
    )
    projects = ProjectService(db)
    project = await projects.get_or_404(project_id)
    await projects.merge_settings(
        project,
        {
            "forge_kind": "github_app",
            "github_repository_url": f"https://github.com/{full_name}",
        },
    )
    reopened = await TaskService(db).forget_open_prs(project_id)
    return {
        "connected": True,
        "repo": full_name,
        "account": repo["owner"]["login"],
        "moved": True,
        "reopened_prs": reopened,
    }
