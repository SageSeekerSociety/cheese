"""Signing in to the desktop app with a provider, which happens in the browser.

The provider's page is shown in the browser, where the person's accounts are
signed in and where Google agrees to show it at all (RFC 8252). The app keeps a
verifier and opens the browser with its hash. Once signed in there, the page
asks for a code bound to that hash (``POST /users/auth/app-sign-in``) and sends
it back through a ``cheese://`` link; the app trades code and verifier for a
session of its own (``.../finish``). A code intercepted on the way is useless
without the verifier, which never left the app.
"""

import base64
import hashlib
import hmac

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_user_auth_service
from app.api.routes.users.auth import _require_same_origin
from app.api.routes.users_common import issue_session
from app.auth.checker import require_auth_user
from app.auth.core import AuthUserInfo
from app.common.auth import (
    APP_SIGN_IN_TTL_S,
    create_app_sign_in_code,
    verify_app_sign_in_code,
)
from app.core.db import get_db
from app.core.errors import AuthenticationRequiredError, InternalServerError
from app.core.sentences import say
from app.core.single_use_state import SingleUseUnavailableError, claim, reserve
from app.domain.user.services import UserAuthService

router = APIRouter(prefix="/users", tags=["Users"])

_SCOPE = "app_sign_in"


class AppSignInStart(BaseModel):
    challenge: str = Field(min_length=43, max_length=43, pattern=r"^[A-Za-z0-9_-]+$")


class AppSignInFinish(BaseModel):
    code: str = Field(min_length=1, max_length=2048)
    verifier: str = Field(min_length=43, max_length=128)


@router.post("/auth/app-sign-in", summary="Hand this sign-in to the desktop app")
async def start_app_sign_in(
    payload: AppSignInStart,
    auth_user: AuthUserInfo = Depends(require_auth_user),
) -> dict:
    code = create_app_sign_in_code(auth_user.user_id, payload.challenge)
    claims = verify_app_sign_in_code(code)
    assert claims is not None
    try:
        await reserve(_SCOPE, claims.jti, ttl_s=APP_SIGN_IN_TTL_S)
    except SingleUseUnavailableError:
        raise InternalServerError(say("appSignInUnavailable")) from None
    return {"code": 200, "message": "OK", "data": {"code": code}}


@router.post("/auth/app-sign-in/finish", summary="Take over a browser sign-in")
async def finish_app_sign_in(
    payload: AppSignInFinish,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_db),
    auth_service: UserAuthService = Depends(get_user_auth_service),
) -> dict:
    _require_same_origin(request)
    refused = AuthenticationRequiredError(say("appSignInExpired"))
    claims = verify_app_sign_in_code(payload.code)
    if claims is None:
        raise refused
    digest = hashlib.sha256(payload.verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    if not hmac.compare_digest(challenge, claims.challenge):
        raise refused
    try:
        if not await claim(_SCOPE, claims.jti):
            raise refused
    except SingleUseUnavailableError:
        raise InternalServerError(say("appSignInUnavailable")) from None
    try:
        user, _profile = await auth_service.get_user_with_profile(claims.user_id)
    except ValueError:
        raise refused from None
    await issue_session(
        response,
        request,
        session,
        user_id=user.id,
        handle=user.username,
        login_method="app_sign_in",
    )
    await session.commit()
    return {"code": 200, "message": "OK", "data": None}
