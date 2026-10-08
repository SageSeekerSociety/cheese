"""第三方登录：授权跳转、回调、决策页建号 / 绑定、连接管理之外的 OAuth 邮箱验证。"""

import hmac
import json
import secrets
import time
import uuid
from typing import Annotated
from urllib.parse import urlencode, urlparse

import jwt
from fastapi import (
    APIRouter,
    Body,
    Depends,
    Form,
    Path,
    Query,
    Request,
)
from fastapi.responses import RedirectResponse
from redis.asyncio import Redis as AsyncRedis
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    get_oauth_service,
    get_user_auth_service,
)
from app.api.routes.legal import client_context
from app.api.routes.users._common import (
    OAuthEmailCodeRequest,
    OAuthEmailVerifyRequest,
    _check_email_code,
    _invite_code_error,
    _issue_2fa_pending_token,
    _normalize_registration_invite_code,
    _own_email,
    _send_email_code,
    _signup_consent,
    _trusted_device,
    logger,
)
from app.api.routes.users_common import (
    issue_session,
)
from app.core import single_use_state
from app.core.config import settings
from app.core.errors import (
    AuthenticationRequiredError,
    BadRequestError,
    ConflictError,
    NotFoundError,
    UnprocessableEntityError,
)
from app.core.single_use_state import SingleUseUnavailableError
from app.db.session import get_db
from app.domain.identity.handles import is_reserved_username
from app.domain.invite.services import InviteCodeService
from app.domain.legal.services import ConsentService
from app.domain.oauth.services import OAuthService
from app.domain.user.login_security import LoginDelay, TOTPService
from app.domain.user.passwords import require_new_password
from app.domain.user.services import (
    NICKNAME_MAX_LENGTH,
    USERNAME_MAX_LENGTH,
    UserAuthService,
    is_valid_username,
    normalize_nickname,
)

router = APIRouter(prefix="/users", tags=["Users"])


@router.get(
    "/auth/oauth/providers",
    summary="List available OAuth providers",
)
async def get_oauth_providers(
    oauth_service: OAuthService = Depends(get_oauth_service),
) -> dict:
    providers = oauth_service.get_providers_config()
    return {
        "code": 200,
        "message": "OK",
        "data": {"providers": providers},
    }


def _oauth_frontend_url(path: str, **params: str | None) -> str:
    """Build a frontend landing URL (success/error) for the browser redirect."""
    query = urlencode({k: v for k, v in params.items() if v is not None})
    return f"{settings.frontend_url}{path}" + (f"?{query}" if query else "")


# --- OAuth client-completion flow (reference contract) --------------------
# When the callback cannot resolve the account by itself it hands the browser
# to a frontend page with either a signed stateToken (decision page) or a
# Redis-backed pending session (credential-verify page). 15-minute TTL both,
# and each is redeemable once: the stateToken's ``jti`` is reserved when it is
# minted and claimed by whichever create/bind request spends it.

_OAUTH_STATE_TTL_S = 15 * 60


_OAUTH_STATE_SCOPE = "oauth_state"


_OAUTH_PENDING_PREFIX = "oauth:pending:"


async def _issue_oauth_state_token(provider_id: str, user_info: dict) -> str:
    """Mint a decision-page stateToken and reserve it for one redemption.

    Raises ``SingleUseUnavailableError`` when Redis is unreachable: a token we
    could not reserve would be refused by every endpoint that spends it.
    """
    now = int(time.time())
    jti = uuid.uuid4().hex
    token = jwt.encode(
        {
            "type": "oauth_state",
            "provider": provider_id,
            "info": user_info,
            "jti": jti,
            "iat": now,
            "exp": now + _OAUTH_STATE_TTL_S,
        },
        settings.jwt_secret,
        algorithm="HS256",
    )
    await single_use_state.reserve(_OAUTH_STATE_SCOPE, jti, ttl_s=_OAUTH_STATE_TTL_S)
    return token


def _decode_oauth_state_token(token: str) -> tuple[str, dict, str]:
    """(provider, userInfo, jti). Reading does not spend the token."""
    try:
        claims = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise AuthenticationRequiredError(
            "Invalid or expired OAuth state token"
        ) from exc
    jti = claims.get("jti")
    if claims.get("type") != "oauth_state" or not isinstance(jti, str) or not jti:
        raise AuthenticationRequiredError("Invalid or expired OAuth state token")
    return str(claims["provider"]), dict(claims["info"]), jti


def _reissue_oauth_state_token(token: str, **info: str) -> str:
    """The same stateToken — same ``jti``, same expiry — with ``info`` added
    to its userInfo. Spending either one spends both."""
    claims = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    claims["info"] = {**claims["info"], **info}
    return jwt.encode(claims, settings.jwt_secret, algorithm="HS256")


async def _redeem_oauth_state_token(jti: str) -> bool:
    """Spend a decoded stateToken. True exactly once; fails closed."""
    try:
        return await single_use_state.claim(_OAUTH_STATE_SCOPE, jti)
    except SingleUseUnavailableError:
        logger.exception("oauth: cannot claim state token")
        return False


async def _spend_oauth_password_attempt(username: str) -> bool:
    """Count one attempt against ``username`` before a credential check.
    False while the wait earlier failures started is running, and the request
    must be refused.

    The same count password login uses, so proving a password through an
    OAuth binding page cannot skip the wait.
    """
    redis = AsyncRedis.from_url(settings.redis_url, decode_responses=False)
    try:
        return (await LoginDelay(redis).admit(username)).admitted
    finally:
        await redis.aclose()


async def _clear_oauth_password_attempts(username: str) -> None:
    redis = AsyncRedis.from_url(settings.redis_url, decode_responses=False)
    try:
        await LoginDelay(redis).clear(username)
    finally:
        await redis.aclose()


def _oauth_too_many_attempts_redirect() -> RedirectResponse:
    return _oauth_error_redirect(
        "TOO_MANY_ATTEMPTS", "Too many failed attempts. Try again later"
    )


def _oauth_user_info_dict(user_info) -> dict:
    return {
        "id": user_info.id,
        "email": user_info.email,
        "name": user_info.name,
        "username": user_info.username,
        "preferredUsername": user_info.preferred_username,
    }


async def _store_oauth_pending(session_id: str, data: dict) -> None:
    redis = AsyncRedis.from_url(settings.redis_url, decode_responses=True)
    try:
        await redis.setex(
            f"{_OAUTH_PENDING_PREFIX}{session_id}", _OAUTH_STATE_TTL_S, json.dumps(data)
        )
    finally:
        await redis.aclose()


async def _pop_oauth_pending(session_id: str) -> dict | None:
    """Fetch-and-delete (one-shot; replay protection)."""
    redis = AsyncRedis.from_url(settings.redis_url, decode_responses=True)
    try:
        key = f"{_OAUTH_PENDING_PREFIX}{session_id}"
        pipe = redis.pipeline()
        pipe.get(key)
        pipe.delete(key)
        raw, _ = await pipe.execute()
        return json.loads(raw) if raw else None
    finally:
        await redis.aclose()


async def _start_oauth_ownership(
    provider_id: str, user_info: dict, owner
) -> dict[str, str]:
    """Hand an identity whose email belongs to ``owner`` to the verify page,
    where the person proves they hold that account before it is linked —
    never linked on the email alone. Returns the verify page's query."""
    session_id = (
        f"oauth_password_{provider_id}_{user_info.get('id')}_"
        f"{int(time.time() * 1000)}_{secrets.token_hex(4)}"
    )
    await _store_oauth_pending(
        session_id,
        {
            "type": "password",
            "providerId": provider_id,
            "userInfo": user_info,
            "userId": owner.id,
            "username": owner.username,
        },
    )
    return {"type": "password", "email": owner.username, "sessionId": session_id}


async def _suggest_oauth_identity(auth_service, user_info: dict) -> tuple[str, str]:
    """(suggestedUsername, suggestedNickname), both passing the rules the
    create form is checked against, the username de-duplicated."""
    base_raw = (
        user_info.get("preferredUsername")
        or user_info.get("username")
        or user_info.get("name")
        or f"user_{user_info.get('id')}"
    )
    suffix_length = 7  # "_" + token_hex(3)
    base = (
        "".join(
            ch for ch in str(base_raw) if ch.isascii() and (ch.isalnum() or ch in "_-")
        )[: USERNAME_MAX_LENGTH - suffix_length]
        or "user"
    )
    username = base
    # A reserved name is suggested exactly as readily as a taken one — the
    # provider's `preferredUsername` could be "system" — so treat it the same
    # way here instead of letting the user hit the wall on submit (#345).
    while (
        not is_valid_username(username)
        or await auth_service.is_username_taken(username)
        or is_reserved_username(username)
    ):
        username = f"{base}_{secrets.token_hex(3)}"
    raw_nickname = str(
        user_info.get("name") or user_info.get("preferredUsername") or username
    )
    try:
        nickname = normalize_nickname(raw_nickname[:NICKNAME_MAX_LENGTH])
    except UnprocessableEntityError:
        nickname = username
    return username, nickname


async def _oauth_login_redirect(
    request: Request,
    session: AsyncSession,
    auth_service,
    user_id: int,
    provider_id: str,
    **extra: str | None,
) -> RedirectResponse:
    """Sign in a resolved OAuth login and land on the success page.

    An account with 2FA gets a 2FA ticket and the verify page instead, exactly
    as a password login would: the provider stands in for the password only,
    and a trusted browser skips the second step here as it does there.
    """
    trust = None
    if await TOTPService(session).is_2fa_enabled(user_id):
        trust = await _trusted_device(request, session, user_id)
        if trust is None:
            return RedirectResponse(
                _oauth_frontend_url(
                    settings.frontend_2fa_verify_path,
                    token=await _issue_2fa_pending_token(user_id),
                ),
                status_code=302,
            )

    user_obj, _profile = await auth_service.get_user_with_profile(user_id)
    redirect = RedirectResponse(
        _oauth_frontend_url(
            settings.frontend_oauth_success_path,
            email=user_obj.email or user_obj.username,
            provider=provider_id,
            **extra,
        ),
        status_code=302,
    )
    # Only the refresh cookie rides the redirect. A token in the URL would
    # land in the browser history, the Referer header and access logs; the
    # landing page trades the cookie for one instead.
    await issue_session(
        redirect,
        request,
        session,
        user_id=user_id,
        handle=user_obj.username,
        login_method=f"oauth:{provider_id}",
        trust_device_id=trust.id if trust else None,
    )
    return redirect


def _oauth_error_redirect(error_code: str, message: str) -> RedirectResponse:
    return RedirectResponse(
        _oauth_frontend_url(
            settings.frontend_oauth_error_path, error_code=error_code, error=message
        ),
        status_code=302,
    )


# The login `state` is ours, never the client's. It is reserved server-side so
# the callback can spend it once, and bound to the browser that started the
# flow by an httpOnly cookie: a callback URL carried to another browser arrives
# without the cookie and is refused. SameSite=Lax still sends the cookie on the
# provider's top-level GET redirect back to us.
_OAUTH_LOGIN_STATE_COOKIE = "OAUTH_STATE"


_OAUTH_LOGIN_STATE_SCOPE = "oauth_login_state"


_OAUTH_LOGIN_STATE_TTL_S = 10 * 60


def _oauth_login_state_scope(provider_id: str) -> str:
    # Per provider, so a state issued for one provider cannot be spent at
    # another provider's callback.
    return f"{_OAUTH_LOGIN_STATE_SCOPE}:{provider_id}"


async def _spend_oauth_login_state(
    provider_id: str, state: str | None, cookie_state: str | None
) -> bool:
    """True when ``state`` is the one this browser was issued and is unspent."""
    if not state or not cookie_state:
        return False
    if not hmac.compare_digest(state.encode(), cookie_state.encode()):
        return False
    try:
        return await single_use_state.claim(
            _oauth_login_state_scope(provider_id), state
        )
    except SingleUseUnavailableError:
        logger.exception("OAuth callback: cannot claim login state for %s", provider_id)
        return False


def _oauth_callback_error_redirect(provider_id: str, message: str) -> RedirectResponse:
    return RedirectResponse(
        _oauth_frontend_url(
            settings.frontend_oauth_error_path, message=message, provider=provider_id
        ),
        status_code=302,
    )


@router.get(
    "/auth/oauth/login/{providerId}",
    summary="Redirect to the OAuth provider's authorization page",
)
async def get_oauth_login_url(
    provider_id: Annotated[str, Path(alias="providerId")],
    oauth_service: OAuthService = Depends(get_oauth_service),
) -> RedirectResponse:
    # The frontend navigates the browser straight to this endpoint, so we
    # 302-redirect to the provider's authorization page.
    state = secrets.token_urlsafe(32)
    try:
        provider = oauth_service.get_provider(provider_id)
        auth_url = oauth_service.generate_authorization_url(provider_id, state)
    except NotFoundError:
        raise NotFoundError(
            f"OAuth provider '{provider_id}' not found or not enabled"
        ) from None

    try:
        await single_use_state.reserve(
            _oauth_login_state_scope(provider_id),
            state,
            ttl_s=_OAUTH_LOGIN_STATE_TTL_S,
        )
    except SingleUseUnavailableError:
        logger.exception("OAuth login: cannot reserve state for %s", provider_id)
        return _oauth_callback_error_redirect(provider_id, "oauth_failed")

    redirect = RedirectResponse(auth_url, status_code=302)
    redirect.set_cookie(
        _OAUTH_LOGIN_STATE_COOKIE,
        state,
        max_age=_OAUTH_LOGIN_STATE_TTL_S,
        httponly=True,
        secure=settings.environment not in ("development", "test"),
        samesite="lax",
        # The provider sends the browser to exactly this URL, so its path is
        # the callback path as the browser sees it, gateway mount included.
        path=urlparse(provider.config.redirect_url).path or "/",
    )
    return redirect


@router.get(
    "/auth/oauth/callback/{providerId}",
    summary="Handle OAuth callback and log the user in",
)
async def handle_oauth_callback(
    provider_id: Annotated[str, Path(alias="providerId")],
    request: Request,
    code: str = Query(...),
    state: str | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    oauth_service: OAuthService = Depends(get_oauth_service),
    auth_service: UserAuthService = Depends(get_user_auth_service),
) -> RedirectResponse:
    # Step 0: the state must be the one issued to this browser, unspent —
    # checked before the code is exchanged.
    if not await _spend_oauth_login_state(
        provider_id, state, request.cookies.get(_OAUTH_LOGIN_STATE_COOKIE)
    ):
        logger.info("OAuth callback: state rejected for %s", provider_id)
        return _oauth_callback_error_redirect(provider_id, "invalid_state")

    # Step 1: exchange the code and fetch the provider profile. Any failure here
    # is an authentication problem — bounce to the frontend error page.
    try:
        _access_token, user_info = await oauth_service.handle_callback(
            provider_id=provider_id,
            code=code,
        )
    except Exception:
        logger.exception("OAuth callback: provider exchange failed for %s", provider_id)
        return _oauth_callback_error_redirect(provider_id, "oauth_failed")

    # Step 2 — resolve the local account (reference contract):
    #   A. existing binding → straight to the success page.
    #   B. the provider email already belongs to a local account → the user must
    #      prove ownership on the verify page (Redis pending session — never
    #      silently link by email).
    #   C. unknown identity → the decision page (signed stateToken) lets the
    #      user create an account or bind an existing one.
    try:
        existing = await oauth_service.get_connection_by_provider(
            provider_id=provider_id,
            provider_user_id=user_info.id,
        )
        if existing:
            return await _oauth_login_redirect(
                request, session, auth_service, existing["userId"], provider_id
            )

        info_dict = _oauth_user_info_dict(user_info)

        conflict_user = None
        if user_info.email:
            conflict_user = await auth_service.get_user_by_email(user_info.email)

        if conflict_user is not None:
            return RedirectResponse(
                _oauth_frontend_url(
                    settings.frontend_oauth_verify_path,
                    **await _start_oauth_ownership(
                        provider_id, info_dict, conflict_user
                    ),
                ),
                status_code=302,
            )

        state_token = await _issue_oauth_state_token(provider_id, info_dict)
        return RedirectResponse(
            _oauth_frontend_url(
                settings.frontend_oauth_complete_path, stateToken=state_token
            ),
            status_code=302,
        )
    except Exception:
        await session.rollback()
        logger.exception(
            "OAuth callback: account resolution failed for %s", provider_id
        )
        return RedirectResponse(
            _oauth_frontend_url(
                settings.frontend_oauth_error_path,
                message="account_error",
                provider=provider_id,
            ),
            status_code=302,
        )


@router.get(
    "/auth/oauth/state",
    summary="Decode the OAuth decision-page state token",
)
async def get_oauth_state(
    token: str = Query(...),
    auth_service: UserAuthService = Depends(get_user_auth_service),
) -> dict:
    provider_id, user_info, _jti = _decode_oauth_state_token(token)
    suggested_username, suggested_nickname = await _suggest_oauth_identity(
        auth_service, user_info
    )
    return {
        "code": 200,
        "message": "Get OAuth state successfully.",
        "data": {
            "providerId": provider_id,
            "userInfo": user_info,
            "suggestedUsername": suggested_username,
            "suggestedNickname": suggested_nickname,
        },
    }


@router.post(
    "/auth/oauth/email/code",
    summary="Send a code to the email a new OAuth account will hold",
)
async def send_oauth_email_code(
    payload: OAuthEmailCodeRequest, request: Request
) -> dict:
    _decode_oauth_state_token(payload.state_token)
    await _send_email_code(request, _own_email(payload.email))
    return {"code": 200, "message": "Verification code sent."}


@router.post(
    "/auth/oauth/email/verify",
    summary="Verify the email a new OAuth account will hold",
)
async def verify_oauth_email(
    payload: OAuthEmailVerifyRequest,
    request: Request,
    auth_service: UserAuthService = Depends(get_user_auth_service),
) -> dict:
    """Answers with either the stateToken again, now carrying the verified
    address that account creation requires, or — when the address belongs to
    an account already — the verify page where that account is proven and
    linked, exactly as for a provider email that matches one."""
    provider_id, user_info, jti = _decode_oauth_state_token(payload.state_token)
    email = _own_email(payload.email)
    await _check_email_code(request, email, payload.code)

    owner = await auth_service.get_user_by_email(email)
    if owner is None:
        return {
            "code": 200,
            "message": "Email verified.",
            "data": {
                "stateToken": _reissue_oauth_state_token(
                    payload.state_token, verifiedEmail=email
                )
            },
        }
    if not await _redeem_oauth_state_token(jti):
        raise AuthenticationRequiredError("Invalid or expired OAuth state token")
    return {
        "code": 200,
        "message": "Email belongs to an existing account.",
        "data": {
            "ownership": await _start_oauth_ownership(provider_id, user_info, owner)
        },
    }


async def _complete_oauth_binding(
    *,
    request: Request,
    session: AsyncSession,
    auth_service: UserAuthService,
    oauth_service: OAuthService,
    user_id: int,
    provider_id: str,
    user_info: dict,
    **extra: str | None,
) -> RedirectResponse:
    """Create the provider↔user connection (idempotence guard) and log in.

    When the identity belongs to someone else, everything this request wrote
    is rolled back, so an account created for the binding does not outlive it.
    """
    provider_user_id = str(user_info.get("id"))
    existing = await oauth_service.get_connection_by_provider(
        provider_id=provider_id, provider_user_id=provider_user_id
    )
    if existing is None:
        try:
            await oauth_service.create_connection(
                user_id=user_id,
                provider_id=provider_id,
                provider_user_id=provider_user_id,
                raw_profile={
                    "email": user_info.get("email"),
                    "name": user_info.get("name"),
                },
            )
        except ConflictError:
            # Linked by a concurrent request since the lookup above.
            existing = await oauth_service.get_connection_by_provider(
                provider_id=provider_id, provider_user_id=provider_user_id
            )
    if existing is not None and existing["userId"] != user_id:
        await session.rollback()
        return _oauth_error_redirect(
            "ALREADY_LINKED", "This OAuth account is linked to another user"
        )
    return await _oauth_login_redirect(
        request, session, auth_service, user_id, provider_id, **extra
    )


@router.post(
    "/auth/oauth/verify",
    summary="Prove ownership of an email-conflicting account (verify page)",
)
async def oauth_verify_conflict(
    request: Request,
    payload: dict = Body(default={}),
    session: AsyncSession = Depends(get_db),
    auth_service: UserAuthService = Depends(get_user_auth_service),
    oauth_service: OAuthService = Depends(get_oauth_service),
) -> RedirectResponse:
    """Reference contract: responds with a 302 on success AND failure — the
    verify page follows the redirect to the success/error landing page."""
    session_id = payload.get("sessionId") or ""
    pending = await _pop_oauth_pending(session_id) if session_id else None
    if not pending or pending.get("type") != "password":
        return _oauth_error_redirect("SESSION_EXPIRED", "OAuth session expired")

    user_id = int(pending["userId"])
    if not await _spend_oauth_password_attempt(pending["username"]):
        return _oauth_too_many_attempts_redirect()
    try:
        password = payload.get("password") or ""
        user, _profile = await auth_service.get_user_with_profile(user_id)
        if not await auth_service.verify_password(user, password):
            return _oauth_error_redirect("INVALID_PASSWORD", "Invalid password")

        await _clear_oauth_password_attempts(pending["username"])
        return await _complete_oauth_binding(
            request=request,
            session=session,
            auth_service=auth_service,
            oauth_service=oauth_service,
            user_id=user_id,
            provider_id=pending["providerId"],
            user_info=pending["userInfo"],
            linked="true",
        )
    except Exception:
        await session.rollback()
        logger.exception("OAuth verify: binding failed")
        return _oauth_error_redirect("VERIFICATION_FAILED", "Verification failed")


@router.post(
    "/oauth/create",
    summary="Create a new account from the OAuth decision page (form post)",
)
async def oauth_create_user(
    request: Request,
    stateToken: str = Form(...),
    username: str = Form(...),
    nickname: str = Form(...),
    passwordMode: str = Form(default="none"),
    password: str | None = Form(default=None),
    inviteCode: str | None = Form(default=None),
    consentTerms: str | None = Form(default=None),
    consentPrivacy: str | None = Form(default=None),
    consentMethod: str | None = Form(default=None),
    session: AsyncSession = Depends(get_db),
    auth_service: UserAuthService = Depends(get_user_auth_service),
    oauth_service: OAuthService = Depends(get_oauth_service),
) -> RedirectResponse:
    try:
        provider_id, user_info, jti = _decode_oauth_state_token(stateToken)
    except AuthenticationRequiredError:
        return _oauth_error_redirect("TOKEN_EXPIRED", "Session expired")
    # Set only by /auth/oauth/email/verify: every account starts with an
    # address it can be recovered through, proven by a code sent to it.
    email = user_info.get("verifiedEmail")
    if not email:
        return _oauth_error_redirect("EMAIL_UNVERIFIED", "Email not verified")

    if not is_valid_username(username):
        return _oauth_error_redirect("INVALID_USERNAME", "Invalid username format")
    try:
        nickname = normalize_nickname(nickname)
    except UnprocessableEntityError as exc:
        return _oauth_error_redirect("INVALID_NICKNAME", str(exc))
    if passwordMode not in ("none", "password"):
        return _oauth_error_redirect("INVALID_AUTH_MODE", "Invalid auth mode")
    consent = {
        k: v
        for k, v in (("terms", consentTerms), ("privacy", consentPrivacy))
        if v is not None
    }
    consent_problem = _signup_consent(consent, consentMethod)
    if consent_problem:
        return _oauth_error_redirect("CONSENT_REQUIRED", consent_problem)
    assert consentMethod is not None
    if passwordMode == "password":
        try:
            require_new_password(password or "")
        except (BadRequestError, UnprocessableEntityError) as exc:
            return _oauth_error_redirect("WEAK_PASSWORD", str(exc))
    # Same gate as /users registration: an OAuth account is still a new account.
    try:
        invite_code = _normalize_registration_invite_code(
            inviteCode, required=settings.require_invite_code
        )
    except UnprocessableEntityError as exc:
        return _oauth_error_redirect("INVITE_CODE_REQUIRED", str(exc))
    invite_service = InviteCodeService(session)
    if invite_code:
        try:
            await invite_service.validate_code(invite_code)
        except ValueError as exc:
            return _oauth_error_redirect(
                "INVALID_INVITE_CODE", str(_invite_code_error(exc))
            )
    if is_reserved_username(username):
        # Ahead of the try/except below on purpose: that block catches
        # Exception broadly and answers with a generic "creation failed" plus an
        # exception log, which is the wrong shape for a name the user can simply
        # change (#345).
        return _oauth_error_redirect("USERNAME_RESERVED", "Username is reserved")
    if await auth_service.is_username_taken(username):
        return _oauth_error_redirect("USERNAME_TAKEN", "Username already taken")
    if not await _redeem_oauth_state_token(jti):
        return _oauth_error_redirect("TOKEN_EXPIRED", "Session expired")
    # Before the account is created, not after: a second stateToken for an
    # identity linked meanwhile would otherwise leave an account behind with
    # nothing linked to it.
    if await oauth_service.get_connection_by_provider(
        provider_id=provider_id, provider_user_id=str(user_info.get("id"))
    ):
        return _oauth_error_redirect(
            "ALREADY_LINKED", "This OAuth account is linked to another user"
        )
    # Registered by someone else since it was verified.
    owner = await auth_service.get_user_by_email(email)
    if owner is not None:
        return RedirectResponse(
            _oauth_frontend_url(
                settings.frontend_oauth_verify_path,
                **await _start_oauth_ownership(provider_id, user_info, owner),
            ),
            status_code=302,
        )

    try:
        try:
            user, _profile = await auth_service.register_oauth_decision(
                email=email,
                username=username,
                nickname=nickname,
                password=password if passwordMode == "password" else None,
            )
        except ValueError as exc:
            # Lost a race the checks above could not see.
            if str(exc) == "USERNAME_TAKEN":
                return _oauth_error_redirect("USERNAME_TAKEN", "Username already taken")
            if str(exc) == "EMAIL_TAKEN":
                return _oauth_error_redirect("EMAIL_TAKEN", "Email already registered")
            raise
        ip, user_agent = client_context(request)
        await ConsentService(session).record(
            user_id=user.id,
            accepted=consent,
            method=consentMethod,
            entry="oauth_signup",
            ip=ip,
            user_agent=user_agent,
        )
        if invite_code:
            try:
                await invite_service.consume_code(invite_code)
            except ValueError as exc:
                await session.rollback()
                return _oauth_error_redirect(
                    "INVALID_INVITE_CODE", str(_invite_code_error(exc))
                )
        response = await _complete_oauth_binding(
            request=request,
            session=session,
            auth_service=auth_service,
            oauth_service=oauth_service,
            user_id=user.id,
            provider_id=provider_id,
            user_info=user_info,
            created="true",
            authMode=passwordMode,
        )
        # The landing page signs in and asks for pending consents as soon as it
        # follows this redirect, which dependency teardown would commit after.
        await session.commit()
        return response
    except Exception:
        await session.rollback()
        logger.exception("OAuth create: account creation failed")
        return _oauth_error_redirect("CREATION_FAILED", "Account creation failed")


@router.post(
    "/oauth/bind",
    summary="Bind OAuth to an existing account (decision page, form post)",
)
async def oauth_bind_user(
    request: Request,
    stateToken: str = Form(...),
    username: str = Form(...),
    password: str = Form(default=""),
    session: AsyncSession = Depends(get_db),
    auth_service: UserAuthService = Depends(get_user_auth_service),
    oauth_service: OAuthService = Depends(get_oauth_service),
) -> RedirectResponse:
    try:
        provider_id, user_info, jti = _decode_oauth_state_token(stateToken)
    except AuthenticationRequiredError:
        return _oauth_error_redirect("TOKEN_EXPIRED", "Session expired")
    if not await _redeem_oauth_state_token(jti):
        return _oauth_error_redirect("TOKEN_EXPIRED", "Session expired")

    if not await _spend_oauth_password_attempt(username):
        return _oauth_too_many_attempts_redirect()

    user = await auth_service._user_repo.get_by_username(username)
    # Unknown users and wrong passwords get the same answer.
    if user is None or not await auth_service.verify_password(user, password):
        return _oauth_error_redirect("INVALID_CREDENTIALS", "Invalid credentials")
    await _clear_oauth_password_attempts(username)

    try:
        return await _complete_oauth_binding(
            request=request,
            session=session,
            auth_service=auth_service,
            oauth_service=oauth_service,
            user_id=user.id,
            provider_id=provider_id,
            user_info=user_info,
            bound="true",
        )
    except Exception:
        await session.rollback()
        logger.exception("OAuth bind: binding failed")
        return _oauth_error_redirect("BINDING_FAILED", "Binding failed")
