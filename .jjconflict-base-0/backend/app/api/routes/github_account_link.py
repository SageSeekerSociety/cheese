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
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Query
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.response import ok
from app.auth.checker import require_auth_user
from app.auth.core import AuthUserInfo
from app.core.config import settings
from app.core.db import get_db
from app.core.errors import InternalServerError
from app.core.github_install_state import (
    ACCOUNT_LINK_TTL_S,
    mint_account_link_state,
    verify_account_link_state,
)
from app.core.single_use_state import SingleUseUnavailableError, claim, reserve
from app.domain.oauth.repositories import OAuthConnectionRepository
from app.domain.oauth.services import OAuthService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/users/me/github-account", tags=["github"])

_PROVIDER_ID = "github_app"
# Namespaces the reservation keys. The repo-install flow (github_install.py)
# carries the same forwardable-state shape and is NOT covered here — see #222.
_LINK_SCOPE = "github_account_link"


async def _oauth_service(db: AsyncSession = Depends(get_db)) -> OAuthService:
    return OAuthService(repo=OAuthConnectionRepository(session=db))


def _link_redirect(
    return_project_id: uuid.UUID | None, **query: str
) -> RedirectResponse:
    path = f"/projects/{return_project_id}/settings" if return_project_id else "/"
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
    minted = mint_account_link_state(
        auth_user.user_id, return_project_id=return_project_id
    )
    try:
        await reserve(_LINK_SCOPE, minted.jti, ttl_s=ACCOUNT_LINK_TTL_S)
    except SingleUseUnavailableError:
        # Refuse rather than hand out a state we cannot retire. Minting it
        # anyway would produce a link that either never works (the claim finds
        # no reservation) or works forever — and "works forever" is the bug.
        logger.exception(
            "github account link: cannot reserve state uid=%s", auth_user.user_id
        )
        raise InternalServerError("暂时无法发起 GitHub 账号连接，请稍后重试") from None
    url = oauth_service.generate_authorization_url(_PROVIDER_ID, minted.state)
    return ok({"url": url})


@router.get("/callback")
async def github_account_link_callback(
    code: str = Query(...),
    state: str = Query(...),
    oauth_service: OAuthService = Depends(_oauth_service),
) -> RedirectResponse:
    # Every exit below logs its outcome with the state's uid and (once known)
    # the arriving GitHub id. The callback 302s on success AND failure, and
    # the redirect lands in whichever browser followed the link — which may
    # not be the uid's own (states travel when users paste the authorize URL
    # into a chat). This log line is the only server-side record of what
    # actually happened; a real incident was undiagnosable without it.
    claims = verify_account_link_state(state)
    if claims is None:
        logger.info("github account link: invalid state")
        return _link_redirect(None, github_account="error", reason="invalid_state")

    try:
        first_use = await claim(_LINK_SCOPE, claims.jti)
    except SingleUseUnavailableError:
        logger.exception(
            "github account link: cannot claim state uid=%s", claims.user_id
        )
        return _link_redirect(
            claims.return_project_id, github_account="error", reason="internal_error"
        )
    if not first_use:
        # Either a forwarded link someone else clicked (#222's incident) or the
        # rightful user reloading the callback. Both get the same answer,
        # because from here the two are indistinguishable — and the copy for
        # invalid_state already says 「已失效或被用过了，请重新点一次」.
        #
        # Deliberately NOT bounced to `claims.return_project_id`: whoever is
        # holding a spent state may well be a stranger, and the project id is
        # the one thing in these claims worth not handing them.
        logger.info("github account link: state already spent uid=%s", claims.user_id)
        return _link_redirect(None, github_account="error", reason="invalid_state")

    try:
        provider = oauth_service.get_provider(_PROVIDER_ID)
        token_data = await provider.exchange_code(code)
        access_token = token_data.get("access_token")
        if not access_token:
            raise ValueError("provider response missing access_token")
        user_info = await provider.get_user_info(access_token)
    except Exception:
        logger.exception(
            "github account link: token exchange failed uid=%s", claims.user_id
        )
        return _link_redirect(
            claims.return_project_id, github_account="error", reason="oauth_failed"
        )

    # Only set when this App has "Expire user authorization tokens" enabled
    # on GitHub's side — otherwise the token is long-lived and these are
    # absent from the response.
    expires_in = token_data.get("expires_in")
    token_expires = (
        datetime.now(UTC) + timedelta(seconds=expires_in) if expires_in else None
    )
    refresh_token = token_data.get("refresh_token")

    # `login` is what a person recognises; `id` is a number only GitHub means
    # anything by. The connections serializer reads the login out of here
    # (`raw_profile["login"]`), and this flow used to store only email+name —
    # so the settings page fell back to the bare numeric id for EVERY user
    # ever linked (observed on dev: 「已连接 222958366」).
    profile = {
        "login": user_info.username,
        "email": user_info.email,
        "name": user_info.name,
    }

    existing = await oauth_service.get_connection_by_provider(
        provider_id=_PROVIDER_ID, provider_user_id=user_info.id
    )
    if existing and existing["userId"] != claims.user_id:
        logger.info(
            "github account link: already linked uid=%s github_id=%s owner_uid=%s",
            claims.user_id,
            user_info.id,
            existing["userId"],
        )
        return _link_redirect(
            claims.return_project_id, github_account="error", reason="already_linked"
        )
    if existing:
        # Re-link refreshes the profile too. Without this the fix above would
        # do nothing for anyone who is ALREADY linked — which is everyone —
        # because 重新连接 took this branch and only ever touched tokens. With
        # it, one click repairs the display; no backfill migration needed, and
        # none is possible anyway (the login has to come from GitHub).
        await oauth_service.update_connection_tokens(
            connection_id=existing["id"],
            access_token=access_token,
            refresh_token=refresh_token,
            token_expires=token_expires,
            # 只有真拿到 login 才覆盖已存的 profile。某次交换没带 login 就照写，
            # 会把一个已经好了的名字降级回 None——正是这个 PR 要修的毛病反着来
            # 一遍，而且是在用户主动点「重新连接」想修好它的时候发生。
            raw_profile=profile if user_info.username else None,
        )
        logger.info(
            "github account link: updated uid=%s github_id=%s",
            claims.user_id,
            user_info.id,
        )
    else:
        await oauth_service.create_connection(
            user_id=claims.user_id,
            provider_id=_PROVIDER_ID,
            provider_user_id=user_info.id,
            raw_profile=profile,
            access_token=access_token,
            refresh_token=refresh_token,
            token_expires=token_expires,
        )
        logger.info(
            "github account link: created uid=%s github_id=%s",
            claims.user_id,
            user_info.id,
        )

    return _link_redirect(claims.return_project_id, github_account="success")
