"""Sandbox-facing GitHub token endpoint (#188 minimal item 1).

Root-mounted like ``/llm`` and the other machine-facing routers: everything a
sandbox calls is built as ``{connector_public_base}/<path>``, which maps onto
the backend root, not ``/api``.

The sandbox authenticates with its scoped cheese token and gets back a
read-only GitHub installation token (actions / checks / metadata, ~1h). The
App's private key and its write permissions never leave the backend — see
``app.domain.agent.github_app``.
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
    minter = await github_app_tokens_for_project(project_id, db)
    if minter is None:
        raise GatewayUnavailableError(
            "This project has no connected GitHub repo, or the App is not "
            "configured on this deployment"
        )
    try:
        gh_token, expires_at = await minter.readonly_token()
    except GitHubAppError as exc:
        raise GatewayUnavailableError(str(exc)) from exc
    return ok(
        {
            "token": gh_token,
            "expires_at": expires_at,
            # So an agent reading the payload knows what it can and cannot do
            # with this credential without trial-and-error.
            "permissions": "read-only: actions, checks, metadata",
        }
    )
