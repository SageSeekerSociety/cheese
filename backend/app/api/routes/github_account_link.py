"""Connect a human's own GitHub account via cheesex-app (#192, user-to-server).

Distinct from both:
- the classic "github" OAuth login provider (``oauth_github_client_id``) —
  that authenticates/creates a *session*; this only records which GitHub
  identity an already-logged-in user is.
- the install flow in ``github_install.py`` — that connects a *project* to a
  *repo*; this connects a *person* to their GitHub account (feeds #189
  credit attribution).

Stored as a normal ``UserOAuthConnection`` row with ``provider_id ==
"github_app"``, so the existing ``/users/{userId}/oauth/connections`` list/
unbind endpoints already cover viewing and removing it.
"""

import logging
import uuid
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Query
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.response import ok
from app.auth.checker import require_auth_user
from app.auth.core import AuthUserInfo
from app.core.config import settings
from app.core.db import get_db
from app.core.github_install_state import (
    mint_account_link_state,
    verify_account_link_state,
)
from app.domain.oauth.repositories import OAuthConnectionRepository
from app.domain.oauth.services import OAuthService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/users/me/github-account", tags=["github"])

_PROVIDER_ID = "github_app"


async def _oauth_service(db: AsyncSession = Depends(get_db)) -> OAuthService:
    return OAuthService(repo=OAuthConnectionRepository(session=db))


def _link_redirect(
    return_project_id: uuid.UUID | None, **query: str
) -> RedirectResponse:
    path = f"/project/{return_project_id}/settings" if return_project_id else "/"
    url = f"{settings.frontend_url}{path}"
    if query:
        url = f"{url}?{urlencode(query)}"
    return RedirectResponse(url, status_code=302)


@router.get("/authorize-url")
async def get_github_account_authorize_url(
    return_project_id: uuid.UUID | None = Query(default=None),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    oauth_service: OAuthService = Depends(_oauth_service),
) -> dict:
    state = mint_account_link_state(
        auth_user.user_id, return_project_id=return_project_id
    )
    url = oauth_service.generate_authorization_url(_PROVIDER_ID, state)
    return ok({"url": url})


@router.get("/callback")
async def github_account_link_callback(
    code: str = Query(...),
    state: str = Query(...),
    oauth_service: OAuthService = Depends(_oauth_service),
) -> RedirectResponse:
    claims = verify_account_link_state(state)
    if claims is None:
        return _link_redirect(None, github_account="error", reason="invalid_state")

    try:
        _access_token, user_info = await oauth_service.handle_callback(
            provider_id=_PROVIDER_ID, code=code, state=state
        )
    except Exception:
        logger.exception("github account link: token exchange failed")
        return _link_redirect(
            claims.return_project_id, github_account="error", reason="oauth_failed"
        )

    existing = await oauth_service.get_connection_by_provider(
        provider_id=_PROVIDER_ID, provider_user_id=user_info.id
    )
    if existing and existing["userId"] != claims.user_id:
        return _link_redirect(
            claims.return_project_id, github_account="error", reason="already_linked"
        )
    if not existing:
        await oauth_service.create_connection(
            user_id=claims.user_id,
            provider_id=_PROVIDER_ID,
            provider_user_id=user_info.id,
            raw_profile={"email": user_info.email, "name": user_info.name},
        )

    return _link_redirect(claims.return_project_id, github_account="success")
