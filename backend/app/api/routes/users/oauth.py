"""第三方登录：授权跳转、回调、决策页建号 / 绑定、连接管理之外的 OAuth 邮箱验证。"""

import hmac
import json
import secrets
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, NamedTuple
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
from starlette.requests import HTTPConnection

from app.api.app_return import back_in_app, started_in_app
from app.api.deps import (
    get_oauth_service,
    get_user_auth_service,
)
from app.api.legal_deps import client_context
from app.api.response import ok, typed_response
from app.api.routes.users._common import (
    GithubAppIntentOut,
    GithubAppIntentRequest,
    OAuthEmailCodeRequest,
    OAuthEmailVerifyRequest,
    _check_email_code,
    _invite_code_error,
    _issue_2fa_pending_token,
    _normalize_registration_invite_code,
    _own_email,
    _send_email_code,
    _signup_consent,
    _trusted_device_id,
    logger,
)
from app.api.users_deps import (
    REFRESH_COOKIE,
    issue_session,
)
from app.api.write_access import ROUTE_DECIDES
from app.auth.checker import require_auth_user
from app.auth.core import AuthUserInfo
from app.core import origins, single_use_state
from app.core.config import settings
from app.core.crypto import Purpose, decrypt, encrypt
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
from app.domain.usage import referrals
from app.domain.user.login_security import LoginDelay, TOTPService
from app.domain.user.passwords import require_new_password
from app.domain.user.services import (
    NICKNAME_MAX_LENGTH,
    USERNAME_MAX_LENGTH,
    UserAuthService,
    is_valid_username,
    normalize_nickname,
)
from app.domain.user.sessions import SessionService

router = APIRouter(prefix="/users", tags=["Users"])


@router.get(
    "/auth/oauth/providers",
    summary="List available OAuth providers",
)
async def get_oauth_providers(
    oauth_service: OAuthService = Depends(get_oauth_service),
) -> dict:
    providers = oauth_service.get_providers_config()
    return ok({"providers": providers})


def _oauth_frontend_url(
    request: HTTPConnection, path: str, **params: str | None
) -> str:
    """A frontend landing URL (success/error) for the browser redirect, on the
    origin the browser is on: the sign-in cookie this flow sets is that
    origin's, and a landing elsewhere would find none."""
    query = urlencode({k: v for k, v in params.items() if v is not None})
    return f"{origins.of_request(request)}{path}" + (f"?{query}" if query else "")


# --- OAuth client-completion flow (reference contract) --------------------
# When the callback cannot resolve the account by itself it hands the browser
# to a frontend page with either a signed stateToken (decision page) or a
# Redis-backed pending session (credential-verify page). 15-minute TTL both,
# and each is redeemable once: the stateToken's ``jti`` is reserved when it is
# minted and claimed by whichever create/bind request spends it.

_OAUTH_STATE_TTL_S = 15 * 60


#: cheesex-app's own user authorization for signing in with GitHub.
GITHUB_APP_PROVIDER = "github_app"


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


def _oauth_too_many_attempts_redirect(request: HTTPConnection) -> RedirectResponse:
    return _oauth_error_redirect(
        request, "TOO_MANY_ATTEMPTS", "Too many failed attempts. Try again later"
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


_OAUTH_TOKENS_PREFIX = "oauth:tokens:"


def _held_tokens_key(provider_id: str, provider_user_id: str) -> str:
    return f"{provider_id}:{provider_user_id}"


async def _hold_oauth_tokens(
    provider_id: str, provider_user_id: str, tokens: dict
) -> None:
    """Keep a provider's tokens server-side until the identity is bound.

    Signing in with GitHub stores tokens on the connection used for repository
    access and PRs in the person's name. Between the
    callback and the binding the person may pass through the decision or the
    verify page, and the tokens must not ride along to the browser on either:
    they wait here, sealed, keyed by the identity they belong to."""
    key = _held_tokens_key(provider_id, provider_user_id)
    sealed = encrypt(Purpose.OAUTH_TOKEN, json.dumps(tokens), bound_to=f"held:{key}")
    redis = AsyncRedis.from_url(settings.redis_url, decode_responses=True)
    try:
        await redis.setex(f"{_OAUTH_TOKENS_PREFIX}{key}", _OAUTH_STATE_TTL_S, sealed)
    finally:
        await redis.aclose()


async def _take_oauth_tokens(provider_id: str, provider_user_id: str) -> dict | None:
    """The tokens `_hold_oauth_tokens` kept for this identity, once."""
    key = _held_tokens_key(provider_id, provider_user_id)
    redis = AsyncRedis.from_url(settings.redis_url, decode_responses=True)
    try:
        sealed = await redis.getdel(f"{_OAUTH_TOKENS_PREFIX}{key}")
    finally:
        await redis.aclose()
    if not isinstance(sealed, str):
        return None
    return json.loads(decrypt(Purpose.OAUTH_TOKEN, sealed, bound_to=f"held:{key}"))


def _token_expiry(tokens: dict, field: str = "token_expires") -> datetime | None:
    raw = tokens.get(field)
    return datetime.fromisoformat(raw) if raw else None


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
    trust_id = None
    if await TOTPService(session).is_2fa_enabled(user_id):
        trust_id = await _trusted_device_id(request, session, user_id)
        if trust_id is None:
            return RedirectResponse(
                _oauth_frontend_url(
                    request,
                    settings.frontend_2fa_verify_path,
                    token=await _issue_2fa_pending_token(user_id),
                ),
                status_code=302,
            )

    user_obj, _profile = await auth_service.get_user_with_profile(user_id)
    redirect = RedirectResponse(
        _oauth_frontend_url(
            request,
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
        trust_device_id=trust_id,
    )
    return redirect


def _oauth_error_redirect(
    request: HTTPConnection, error_code: str, message: str
) -> RedirectResponse:
    return RedirectResponse(
        _oauth_frontend_url(
            request,
            settings.frontend_oauth_error_path,
            error_code=error_code,
            error=message,
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


# Connecting GitHub from inside the app is not a sign-in: the person is signed
# in already, and the result is a connection on that account, never a session.
# The app asks for an intent with its own session; the browser that opens the
# intent must hold a sign-in of the same account (its refresh cookie is sent to
# the login route), so a link carried to another browser connects nothing; the
# intent is spent there and carried on as the state the callback reads back.
_GITHUB_INTENT_SCOPE = "github_app_intent"


class _GithubIntent(NamedTuple):
    user_id: int
    # The page to land back on, a path inside the frontend (`_safe_return_path`).
    return_path: str | None
    # Started in the desktop app: the result is shown there (`app_return`).
    in_app: bool


def _safe_return_path(path: object) -> str | None:
    """`path` when it is a path inside the frontend, else None. A landing that
    could take the browser to another host would be an open redirect."""
    if not isinstance(path, str) or not path or len(path) > 512:
        return None
    if not path.startswith("/") or path.startswith("//"):
        return None
    if any(c in path for c in "\\?#\r\n"):
        return None
    return path


def _github_intent_token(intent: _GithubIntent, kind: str, jti: str) -> str:
    now = int(time.time())
    return jwt.encode(
        {
            "type": kind,
            "user_id": intent.user_id,
            "rp": intent.return_path,
            "app": intent.in_app,
            "iat": now,
            "exp": now + _OAUTH_LOGIN_STATE_TTL_S,
            "jti": jti,
        },
        settings.jwt_secret,
        algorithm="HS256",
    )


def _read_github_intent(token: str, kind: str) -> tuple[_GithubIntent, str] | None:
    """The intent a token of ``kind`` carries, with its ``jti``; None when the
    token is not one, has expired or was not signed here."""
    try:
        claims = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None
    if (
        claims.get("type") != kind
        or type(claims.get("user_id")) is not int
        or not isinstance(claims.get("jti"), str)
    ):
        return None
    intent = _GithubIntent(
        user_id=claims["user_id"],
        return_path=_safe_return_path(claims.get("rp")),
        in_app=claims.get("app") is True,
    )
    return intent, claims["jti"]


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


def _oauth_callback_error_redirect(
    request: HTTPConnection, provider_id: str, message: str
) -> RedirectResponse:
    return RedirectResponse(
        _oauth_frontend_url(
            request,
            settings.frontend_oauth_error_path,
            message=message,
            provider=provider_id,
        ),
        status_code=302,
    )


@router.get(
    "/auth/oauth/login/{providerId}",
    summary="Redirect to the OAuth provider's authorization page",
)
async def get_oauth_login_url(
    request: Request,
    provider_id: Annotated[str, Path(alias="providerId")],
    intent: str | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    oauth_service: OAuthService = Depends(get_oauth_service),
) -> RedirectResponse:
    # The frontend navigates the browser straight to this endpoint, so we
    # 302-redirect to the provider's authorization page.
    if intent:
        read = _read_github_intent(intent, "github_intent")
        if provider_id != GITHUB_APP_PROVIDER or read is None:
            return _oauth_callback_error_redirect(request, provider_id, "invalid_state")
        connecting, jti = read
        holder = await SessionService(session).holder(
            request.cookies.get(REFRESH_COOKIE) or ""
        )
        if holder != connecting.user_id:
            # Shown in this browser even when the app started it: signing in
            # here is the way forward, so the app is not where to say so.
            logger.info(
                "GitHub connect: opened without the account's sign-in uid=%s",
                connecting.user_id,
            )
            return _oauth_error_redirect(
                request,
                "GITHUB_CONNECT_SIGNED_OUT",
                "Sign in to the account that started connecting GitHub",
            )
        try:
            spent = await single_use_state.claim(_GITHUB_INTENT_SCOPE, jti)
        except SingleUseUnavailableError:
            logger.exception("GitHub connect: cannot claim intent")
            spent = False
        if not spent:
            return _oauth_callback_error_redirect(request, provider_id, "invalid_state")
        state = _github_intent_token(
            connecting, "github_intent_state", uuid.uuid4().hex
        )
    else:
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
        return _oauth_callback_error_redirect(request, provider_id, "oauth_failed")

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


@router.post(
    "/auth/oauth/github-app/intent",
    dependencies=[ROUTE_DECIDES],
    **typed_response(GithubAppIntentOut),
)
async def github_app_intent(
    request: Request,
    payload: GithubAppIntentRequest,
    auth_user: AuthUserInfo = Depends(require_auth_user),
) -> dict:
    """Tie a GitHub authorization started in the app to the signed-in account:
    the callback connects GitHub to it, or renews the connection it has, and
    lands on ``returnPath``."""
    jti = uuid.uuid4().hex
    await single_use_state.reserve(
        _GITHUB_INTENT_SCOPE, jti, ttl_s=_OAUTH_LOGIN_STATE_TTL_S
    )
    intent = _GithubIntent(
        user_id=auth_user.user_id,
        return_path=_safe_return_path(payload.return_path),
        in_app=started_in_app(request),
    )
    return ok({"intent": _github_intent_token(intent, "github_intent", jti)})


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
        return _oauth_callback_error_redirect(request, provider_id, "invalid_state")

    # Step 1: exchange the code and fetch the provider profile. Any failure here
    # is an authentication problem — bounce to the frontend error page.
    try:
        _access_token, user_info = await oauth_service.handle_callback(
            provider_id=provider_id,
            code=code,
        )
    except Exception:
        logger.exception("OAuth callback: provider exchange failed for %s", provider_id)
        return _oauth_callback_error_redirect(request, provider_id, "oauth_failed")

    return await _resolve_oauth_identity(
        request, session, oauth_service, auth_service, provider_id, user_info
    )


async def _resolve_oauth_identity(
    request: Request,
    session: AsyncSession,
    oauth_service: OAuthService,
    auth_service: UserAuthService,
    provider_id: str,
    user_info,
    tokens: dict | None = None,
) -> RedirectResponse:
    """Sign in the account this provider identity belongs to, or send the
    person to the page that decides which account it becomes.

    `tokens` are the provider's own (GitHub's), stored on the connection the
    sign-in leaves behind."""
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
            if tokens:
                await oauth_service.update_connection_tokens(
                    connection_id=existing["id"],
                    access_token=tokens["access_token"],
                    refresh_token=tokens.get("refresh_token"),
                    token_expires=_token_expiry(tokens),
                    refresh_token_expires=_token_expiry(
                        tokens, "refresh_token_expires"
                    ),
                    raw_profile=_github_profile(user_info),
                )
            return await _oauth_login_redirect(
                request, session, auth_service, existing["userId"], provider_id
            )

        info_dict = _oauth_user_info_dict(user_info)
        if tokens:
            await _hold_oauth_tokens(provider_id, str(user_info.id), tokens)

        conflict_user = None
        if user_info.email:
            conflict_user = await auth_service.get_user_by_email(user_info.email)

        if conflict_user is not None:
            return RedirectResponse(
                _oauth_frontend_url(
                    request,
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
                request, settings.frontend_oauth_complete_path, stateToken=state_token
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
                request,
                settings.frontend_oauth_error_path,
                message="account_error",
                provider=provider_id,
            ),
            status_code=302,
        )


async def _connect_github_to(
    request: HTTPConnection,
    session: AsyncSession,
    oauth_service: OAuthService,
    intent: _GithubIntent,
    user_info,
    tokens: dict,
) -> RedirectResponse:
    """Connect a GitHub identity to the account that started connecting it, or
    renew the connection it has. No password: that account's own sign-in
    minted the intent and opened it. No session either: this is a connection,
    and the browser is signed in already.

    An account that already has a GitHub connection only renews it, so a
    different identity there is a mismatch, never a second connection."""
    existing = await oauth_service.get_connection_by_provider(
        provider_id=GITHUB_APP_PROVIDER, provider_user_id=str(user_info.id)
    )
    if existing is not None and existing["userId"] == intent.user_id:
        await oauth_service.update_connection_tokens(
            connection_id=existing["id"],
            access_token=tokens["access_token"],
            refresh_token=tokens.get("refresh_token"),
            token_expires=_token_expiry(tokens),
            refresh_token_expires=_token_expiry(tokens, "refresh_token_expires"),
            raw_profile=_github_profile(user_info),
        )
    else:
        connections = await oauth_service.list_user_connections(intent.user_id)
        if any(c["providerId"] == GITHUB_APP_PROVIDER for c in connections):
            return _oauth_error_redirect(
                request,
                "ACCOUNT_MISMATCH",
                "This GitHub account is not linked to the Cheese account that started authorization",
            )
        if existing is not None:
            return _oauth_error_redirect(
                request,
                "ALREADY_LINKED",
                "This OAuth account is linked to another user",
            )
        try:
            await oauth_service.create_connection(
                user_id=intent.user_id,
                provider_id=GITHUB_APP_PROVIDER,
                provider_user_id=str(user_info.id),
                raw_profile=_github_profile(user_info),
                access_token=tokens["access_token"],
                refresh_token=tokens.get("refresh_token"),
                token_expires=_token_expiry(tokens),
                refresh_token_expires=_token_expiry(tokens, "refresh_token_expires"),
            )
        except ConflictError:
            # Linked by a concurrent request since the lookup.
            await session.rollback()
            return _oauth_error_redirect(
                request,
                "ALREADY_LINKED",
                "This OAuth account is linked to another user",
            )
    # The landing page reads the connection back as soon as it loads, which
    # dependency teardown would commit after.
    await session.commit()
    return RedirectResponse(
        f"{origins.of_request(request)}"
        f"{intent.return_path or '/users/settings/security'}",
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
    return ok(
        {
            "providerId": provider_id,
            "userInfo": user_info,
            "suggestedUsername": suggested_username,
            "suggestedNickname": suggested_nickname,
        },
        "Get OAuth state successfully.",
    )


@router.post(
    "/auth/oauth/email/code",
    summary="Send a code to the email a new OAuth account will hold",
    dependencies=[ROUTE_DECIDES],
)
async def send_oauth_email_code(
    payload: OAuthEmailCodeRequest, request: Request
) -> dict:
    _decode_oauth_state_token(payload.state_token)
    await _send_email_code(request, _own_email(payload.email))
    return ok(None, "Verification code sent.")


@router.post(
    "/auth/oauth/email/verify",
    summary="Verify the email a new OAuth account will hold",
    dependencies=[ROUTE_DECIDES],
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
        return ok(
            {
                "stateToken": _reissue_oauth_state_token(
                    payload.state_token, verifiedEmail=email
                )
            },
            "Email verified.",
        )
    if not await _redeem_oauth_state_token(jti):
        raise AuthenticationRequiredError("Invalid or expired OAuth state token")
    return ok(
        {"ownership": await _start_oauth_ownership(provider_id, user_info, owner)},
        "Email belongs to an existing account.",
    )


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
    tokens = await _take_oauth_tokens(provider_id, provider_user_id)
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
                    **({"login": user_info.get("username")} if tokens else {}),
                },
                access_token=tokens["access_token"] if tokens else None,
                refresh_token=tokens.get("refresh_token") if tokens else None,
                token_expires=_token_expiry(tokens) if tokens else None,
                refresh_token_expires=_token_expiry(tokens, "refresh_token_expires")
                if tokens
                else None,
            )
        except ConflictError:
            # Linked by a concurrent request since the lookup above.
            existing = await oauth_service.get_connection_by_provider(
                provider_id=provider_id, provider_user_id=provider_user_id
            )
    if existing is not None and existing["userId"] != user_id:
        await session.rollback()
        return _oauth_error_redirect(
            request, "ALREADY_LINKED", "This OAuth account is linked to another user"
        )
    return await _oauth_login_redirect(
        request, session, auth_service, user_id, provider_id, **extra
    )


@router.post(
    "/auth/oauth/verify",
    summary="Prove ownership of an email-conflicting account (verify page)",
    dependencies=[ROUTE_DECIDES],
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
        return _oauth_error_redirect(
            request, "SESSION_EXPIRED", "OAuth session expired"
        )

    user_id = int(pending["userId"])
    if not await _spend_oauth_password_attempt(pending["username"]):
        return _oauth_too_many_attempts_redirect(request)
    try:
        password = payload.get("password") or ""
        user, _profile = await auth_service.get_user_with_profile(user_id)
        if not await auth_service.verify_password(user, password):
            if pending["providerId"] == GITHUB_APP_PROVIDER:
                return _oauth_error_redirect(
                    request,
                    "GITHUB_PASSWORD_VERIFICATION_FAILED",
                    "Password verification failed",
                )
            return _oauth_error_redirect(
                request, "INVALID_PASSWORD", "Invalid password"
            )

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
        return _oauth_error_redirect(
            request, "VERIFICATION_FAILED", "Verification failed"
        )


@router.post(
    "/oauth/create",
    summary="Create a new account from the OAuth decision page (form post)",
    dependencies=[ROUTE_DECIDES],
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
        return _oauth_error_redirect(request, "TOKEN_EXPIRED", "Session expired")
    # Set only by /auth/oauth/email/verify: every account starts with an
    # address it can be recovered through, proven by a code sent to it.
    email = user_info.get("verifiedEmail")
    if not email:
        return _oauth_error_redirect(request, "EMAIL_UNVERIFIED", "Email not verified")

    if not is_valid_username(username):
        return _oauth_error_redirect(
            request, "INVALID_USERNAME", "Invalid username format"
        )
    try:
        nickname = normalize_nickname(nickname)
    except UnprocessableEntityError as exc:
        return _oauth_error_redirect(request, "INVALID_NICKNAME", str(exc))
    if passwordMode not in ("none", "password"):
        return _oauth_error_redirect(request, "INVALID_AUTH_MODE", "Invalid auth mode")
    consent = {
        k: v
        for k, v in (("terms", consentTerms), ("privacy", consentPrivacy))
        if v is not None
    }
    consent_problem = _signup_consent(consent, consentMethod)
    if consent_problem:
        return _oauth_error_redirect(request, "CONSENT_REQUIRED", consent_problem)
    assert consentMethod is not None
    if passwordMode == "password":
        try:
            require_new_password(password or "")
        except (BadRequestError, UnprocessableEntityError) as exc:
            return _oauth_error_redirect(request, "WEAK_PASSWORD", str(exc))
    # Same gate as /users registration: an OAuth account is still a new account.
    try:
        invite_code = _normalize_registration_invite_code(
            inviteCode, required=settings.require_invite_code
        )
    except UnprocessableEntityError as exc:
        return _oauth_error_redirect(request, "INVITE_CODE_REQUIRED", str(exc))
    invite_service = InviteCodeService(session)
    if invite_code:
        try:
            await invite_service.validate_code(invite_code)
        except ValueError as exc:
            return _oauth_error_redirect(
                request, "INVALID_INVITE_CODE", str(_invite_code_error(exc))
            )
    if is_reserved_username(username):
        # Ahead of the try/except below on purpose: that block catches
        # Exception broadly and answers with a generic "creation failed" plus an
        # exception log, which is the wrong shape for a name the user can simply
        # change (#345).
        return _oauth_error_redirect(
            request, "USERNAME_RESERVED", "Username is reserved"
        )
    if await auth_service.is_username_taken(username):
        return _oauth_error_redirect(
            request, "USERNAME_TAKEN", "Username already taken"
        )
    if not await _redeem_oauth_state_token(jti):
        return _oauth_error_redirect(request, "TOKEN_EXPIRED", "Session expired")
    # Before the account is created, not after: a second stateToken for an
    # identity linked meanwhile would otherwise leave an account behind with
    # nothing linked to it.
    if await oauth_service.get_connection_by_provider(
        provider_id=provider_id, provider_user_id=str(user_info.get("id"))
    ):
        return _oauth_error_redirect(
            request, "ALREADY_LINKED", "This OAuth account is linked to another user"
        )
    # Registered by someone else since it was verified.
    owner = await auth_service.get_user_by_email(email)
    if owner is not None:
        return RedirectResponse(
            _oauth_frontend_url(
                request,
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
                return _oauth_error_redirect(
                    request, "USERNAME_TAKEN", "Username already taken"
                )
            if str(exc) == "EMAIL_TAKEN":
                return _oauth_error_redirect(
                    request, "EMAIL_TAKEN", "Email already registered"
                )
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
                consumed = await invite_service.consume_code(invite_code)
            except ValueError as exc:
                await session.rollback()
                return _oauth_error_redirect(
                    request, "INVALID_INVITE_CODE", str(_invite_code_error(exc))
                )
            await referrals.record_referral(session, consumed, user.id)
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
        return _oauth_error_redirect(
            request, "CREATION_FAILED", "Account creation failed"
        )


@router.post(
    "/oauth/bind",
    summary="Bind OAuth to an existing account (decision page, form post)",
    dependencies=[ROUTE_DECIDES],
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
        return _oauth_error_redirect(request, "TOKEN_EXPIRED", "Session expired")
    if not await _redeem_oauth_state_token(jti):
        return _oauth_error_redirect(request, "TOKEN_EXPIRED", "Session expired")

    if not await _spend_oauth_password_attempt(username):
        return _oauth_too_many_attempts_redirect(request)

    user = await auth_service._user_repo.get_by_username(username)
    # Unknown users and wrong passwords get the same answer.
    if user is None or not await auth_service.verify_password(user, password):
        if provider_id == GITHUB_APP_PROVIDER:
            return _oauth_error_redirect(
                request,
                "GITHUB_PASSWORD_VERIFICATION_FAILED",
                "Password verification failed",
            )
        return _oauth_error_redirect(
            request, "INVALID_CREDENTIALS", "Invalid credentials"
        )
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
        return _oauth_error_redirect(request, "BINDING_FAILED", "Binding failed")


def _github_profile(user_info) -> dict:
    """What a GitHub connection keeps of the profile: the login is what the
    settings page and every credit show (`identity.identity_from_profile`)."""
    return {
        "login": user_info.username,
        "email": user_info.email,
        "name": user_info.name,
    }


async def sign_in_with_github(
    request: Request,
    *,
    code: str,
    state: str,
    session: AsyncSession,
    oauth_service: OAuthService,
    auth_service: UserAuthService,
) -> RedirectResponse:
    """Complete a GitHub sign-in at the App's registered callback URL."""
    provider_id = GITHUB_APP_PROVIDER
    if not await _spend_oauth_login_state(
        provider_id, state, request.cookies.get(_OAUTH_LOGIN_STATE_COOKIE)
    ):
        logger.info("GitHub sign-in: state rejected")
        return _oauth_callback_error_redirect(request, provider_id, "invalid_state")
    read = _read_github_intent(state, "github_intent_state") if "." in state else None
    if "." in state and read is None:
        return _oauth_callback_error_redirect(request, provider_id, "invalid_state")
    connecting = read[0] if read else None

    def shown(landing: RedirectResponse) -> RedirectResponse:
        # A connection started in the desktop app shows its result there.
        return (
            back_in_app(request, landing)
            if connecting and connecting.in_app
            else landing
        )

    try:
        provider = oauth_service.get_provider(provider_id)
        token_data = await provider.exchange_code(code)
        access_token = token_data.get("access_token")
        user_info = await provider.get_user_info(access_token) if access_token else None
    except Exception:
        logger.exception("GitHub sign-in: provider exchange failed")
        return shown(
            _oauth_callback_error_redirect(request, provider_id, "oauth_failed")
        )
    if user_info is None:
        logger.info("GitHub sign-in: the exchange returned no access token")
        return shown(
            _oauth_callback_error_redirect(request, provider_id, "oauth_failed")
        )
    # Only present when the App expires its user tokens; otherwise the token
    # is long-lived and both are absent.
    expires_in = token_data.get("expires_in")
    refresh_expires_in = token_data.get("refresh_token_expires_in")
    tokens = {
        "access_token": access_token,
        "refresh_token": token_data.get("refresh_token"),
        "token_expires": (
            (datetime.now(UTC) + timedelta(seconds=expires_in)).isoformat()
            if expires_in
            else None
        ),
        "refresh_token_expires": (
            (datetime.now(UTC) + timedelta(seconds=refresh_expires_in)).isoformat()
            if refresh_expires_in is not None
            else None
        ),
    }
    if connecting is not None:
        return shown(
            await _connect_github_to(
                request, session, oauth_service, connecting, user_info, tokens
            )
        )
    return await _resolve_oauth_identity(
        request, session, oauth_service, auth_service, provider_id, user_info, tokens
    )


@router.get("/me/github-account/callback")
async def github_sign_in_callback(
    request: Request,
    code: str = Query(...),
    state: str = Query(default=""),
    session: AsyncSession = Depends(get_db),
    oauth_service: OAuthService = Depends(get_oauth_service),
    auth_service: UserAuthService = Depends(get_user_auth_service),
) -> RedirectResponse:
    """The App's registered URL completes the same GitHub sign-in as login."""
    return await sign_in_with_github(
        request,
        code=code,
        state=state,
        session=session,
        oauth_service=oauth_service,
        auth_service=auth_service,
    )
