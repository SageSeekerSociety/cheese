"""GitHub App install flow (#192): connect a project to a repo via cheesex-app.

Two routes:
- ``GET /api/projects/{project_id}/github/install-url`` — the frontend calls
  this to get the ``github.com/apps/<slug>/installations/new`` URL to send
  the browser to, carrying a signed ``state`` that proves which project
  asked (``app.core.github_install_state``).
- ``GET /github/app/callback`` — GitHub's setup_url redirect target once the
  human finishes installing (or cancels/requests approval). Verifies
  ``state``, looks up which repo(s) the installation covers, upserts the
  connection, and bounces back to the frontend project settings page.

The callback path is root-mounted (not under /api) because it is dictated by
the App's global setup_url config, not something the frontend can address
per-project — GitHub does not parameterize it.
"""

import asyncio
import logging
import uuid
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Query
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import ActorResolverDep
from app.api.response import ok
from app.core.config import settings
from app.core.db import get_db
from app.core.errors import AuthenticationRequiredError, ConflictError
from app.core.github_install_state import mint_install_state, verify_install_state
from app.domain.agent.github_app import (
    GitHubAppError,
    fetch_installation_repos,
    list_app_installations,
)
from app.domain.project.repositories import (
    ProjectGitInstallationRepository,
    ProjectRepository,
)
from app.domain.project.services import ProjectService
from app.domain.review.github_pr import parse_github_repo
from app.domain.workspace import service as ws

logger = logging.getLogger(__name__)

router = APIRouter(tags=["github"])

# frontend/src/router/index.ts: project-settings is /projects/:projectId/settings
_SETTINGS_ROUTE = "/projects/{project_id}/settings"


def _settings_redirect(project_id: uuid.UUID | None, **query: str) -> RedirectResponse:
    path = (
        _SETTINGS_ROUTE.format(project_id=project_id) if project_id is not None else "/"
    )
    url = f"{settings.frontend_url}{path}"
    if query:
        url = f"{url}?{urlencode(query)}"
    return RedirectResponse(url, status_code=302)


@router.get("/api/projects/{project_id}/github/connection")
async def get_github_connection(
    project_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> dict:
    """The repo this project is currently connected to, if any."""
    await ProjectService(db).get_or_404(project_id)
    installation = await ProjectGitInstallationRepository(db).get_by_project(project_id)
    if installation is None:
        return ok({"connected": False})
    return ok(
        {
            "connected": True,
            "repo": installation.repo,
            "account": installation.account,
        }
    )


@router.post("/api/projects/{project_id}/github/connect")
async def connect_github_repo(
    project_id: uuid.UUID,
    resolver: ActorResolverDep,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Connect via an EXISTING installation when one already covers the
    project's upstream repo; otherwise hand back the install URL.

    GitHub's ``installations/new`` page dead-ends when the App is already
    installed on the org — it shows the installation settings page and never
    fires the setup_url callback, so the signed state is lost and the
    connection is never recorded. The frontend therefore calls this first,
    and only bounces the browser to GitHub when it answers
    ``{connected: false}``.
    """
    actor = await resolver.resolve(fallback_handle=None)
    if not actor.authenticated:
        raise AuthenticationRequiredError("需要登录才能连接 GitHub 仓库")
    await ProjectService(db).get_or_404(project_id)

    # Idempotent: already connected → answer with what we have.
    existing = await ProjectGitInstallationRepository(db).get_by_project(project_id)
    if existing is not None:
        return ok(
            {
                "connected": True,
                "repo": existing.repo,
                "account": existing.account,
            }
        )

    upstream = await asyncio.to_thread(ws.get_upstream, project_id)
    parsed = parse_github_repo(upstream) if upstream else None
    if parsed is not None:
        target = f"{parsed[0]}/{parsed[1]}".lower()
        try:
            installations = await list_app_installations()
        except GitHubAppError:
            installations = []  # App unconfigured / GitHub down → install-url path
        for inst in installations:
            try:
                repos = await fetch_installation_repos(inst["id"])
            except GitHubAppError:
                continue
            for repo in repos:
                if str(repo.get("full_name", "")).lower() != target:
                    continue
                # ConflictError (installation bound to another project)
                # propagates: that genuinely needs a human decision.
                await ProjectGitInstallationRepository(db).upsert(
                    project_id=project_id,
                    installation_id=inst["id"],
                    repo=repo["full_name"],
                    account=repo["owner"]["login"],
                )
                return ok(
                    {
                        "connected": True,
                        "repo": repo["full_name"],
                        "account": repo["owner"]["login"],
                    }
                )

    # No existing installation covers the upstream (or no GitHub upstream at
    # all) — fall back to the interactive install flow.
    state = mint_install_state(project_id)
    url = (
        f"https://github.com/apps/{settings.github_app_slug}/installations/new"
        f"?state={state}"
    )
    return ok({"connected": False, "install_url": url})


@router.get("/api/projects/{project_id}/github/install-url")
async def get_github_install_url(
    project_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> dict:
    """Where to send the browser to install cheesex-app for this project."""
    await ProjectService(db).get_or_404(project_id)
    state = mint_install_state(project_id)
    url = (
        f"https://github.com/apps/{settings.github_app_slug}/installations/new"
        f"?state={state}"
    )
    return ok({"url": url})


@router.get("/github/app/callback")
async def github_app_install_callback(
    installation_id: int | None = Query(default=None),
    setup_action: str | None = Query(default=None),
    state: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    project_id = verify_install_state(state) if state else None
    if project_id is None:
        return _settings_redirect(None, github_install="error", reason="invalid_state")

    if setup_action == "request":
        # A non-admin org member requested the install; an admin still has to
        # approve it on GitHub's side before an installation_id exists.
        return _settings_redirect(project_id, github_install="pending")

    if not installation_id:
        return _settings_redirect(
            project_id, github_install="error", reason="missing_installation_id"
        )

    try:
        project = await ProjectRepository(db).get(project_id)
        if project is None:
            return _settings_redirect(
                project_id, github_install="error", reason="project_not_found"
            )

        repos = await fetch_installation_repos(installation_id)
        if not repos:
            return _settings_redirect(
                project_id, github_install="error", reason="no_accessible_repos"
            )
        repo = repos[0]

        await ProjectGitInstallationRepository(db).upsert(
            project_id=project_id,
            installation_id=installation_id,
            repo=repo["full_name"],
            account=repo["owner"]["login"],
        )
    except ConflictError:
        await db.rollback()
        return _settings_redirect(
            project_id, github_install="error", reason="installation_conflict"
        )
    except GitHubAppError:
        await db.rollback()
        logger.warning(
            "github install callback: could not list repos for installation %s",
            installation_id,
        )
        return _settings_redirect(
            project_id, github_install="error", reason="github_error"
        )
    except Exception:
        await db.rollback()
        logger.exception("github install callback failed for project %s", project_id)
        return _settings_redirect(
            project_id, github_install="error", reason="internal_error"
        )

    return _settings_redirect(
        project_id, github_install="success", repo=repo["full_name"]
    )
