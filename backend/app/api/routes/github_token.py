"""Sandbox-facing GitHub token endpoint (#188 minimal item 1).

Root-mounted like ``/llm`` and the other machine-facing routers: everything a
sandbox calls is built as ``{connector_public_base}/<path>``, which maps onto
the backend root, not ``/api``.

The sandbox authenticates with its scoped cheese token and gets back a GitHub
installation token (~1h) carrying everything the App was granted on this repo —
enough to push a branch and open a PR, not only to read. What exactly that is
depends on the installation's grants, so the payload spells them out rather
than hardcoding a list — see ``app.domain.agent.github_app``. The App's private
key never leaves the backend.
"""

import uuid

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.response import ok
from app.core.db import get_db
from app.core.errors import (
    AuthenticationRequiredError,
    GatewayUnavailableError,
)
from app.core.sandbox_auth import scoped_token_claims
from app.domain.agent.github_app import GitHubAppError, github_app_tokens_for_project
from app.domain.project.repositories import ProjectGitInstallationRepository

router = APIRouter(prefix="/sandbox", tags=["sandbox"])


def _caller_token(request: Request) -> str:
    """The scoped token, however the caller presents it: the cheese CLI sends
    X-Cheese-Token; a bearer header also works."""
    direct = request.headers.get("x-cheese-token", "").strip()
    if direct:
        return direct
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return ""


@router.get("/github-token")
async def sandbox_github_token(
    request: Request, db: AsyncSession = Depends(get_db)
) -> dict:
    token = _caller_token(request)
    claims = scoped_token_claims(token) if token else None
    if not claims or not claims.get("p"):
        raise AuthenticationRequiredError("A scoped cheese token is required")
    project_id = uuid.UUID(claims["p"])
    # The repo's full name comes from the SAME installation row the minter is
    # resolved from, so a token and the repo it works on can never disagree.
    # Without it the caller holds a credential and no idea what to point it
    # at: `gh api repos/:owner/:repo/...` needs a name the sandbox has no
    # other way to learn (the workspace is not a git checkout of the repo).
    installation = await ProjectGitInstallationRepository(db).get_by_project(project_id)
    minter = await github_app_tokens_for_project(project_id, db)
    if minter is None:
        raise GatewayUnavailableError(
            "This project has no connected GitHub repo, or the App is not "
            "configured on this deployment"
        )
    try:
        gh_token, expires_at = await minter.installation_token()
        granted = await minter.granted_permissions()
    except GitHubAppError as exc:
        raise GatewayUnavailableError(str(exc)) from exc
    return ok(
        {
            "token": gh_token,
            "expires_at": expires_at,
            # So an agent reading the payload knows what it can and cannot do
            # with this credential without trial-and-error. Levels are spelled
            # out per permission because the difference between `contents:
            # read` and `contents: write` is the difference between reading the
            # repo and delivering the work. Computed, not hardcoded: the set
            # shrinks or grows with the App's grants, and a stale literal here
            # would send an agent at a 403 it was told to expect success from.
            "permissions": ", ".join(
                f"{name}: {level}" for name, level in sorted(granted.items())
            ),
            #: "owner/repo" this token is scoped to.
            "repo": installation.repo if installation else None,
        }
    )
