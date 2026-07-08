from datetime import UTC, datetime, timedelta
from typing import Annotated

import jwt
from fastapi import Header

from app.core.config import settings
from app.core.errors import AuthenticationRequiredError


def _utcnow() -> datetime:
    # Must stay timezone-aware: .timestamp() on a naive datetime uses the
    # local timezone (TZ=Asia/Shanghai in Docker → 8h offset), which makes
    # every JWT expire on creation. Keep UTC so .timestamp() returns the
    # correct Unix epoch seconds regardless of the container's TZ setting.
    return datetime.now(UTC)


def create_access_token(user_id: int) -> str:
    """Create a short-lived access token for the given user."""
    now = _utcnow()
    payload = {
        "sub": str(user_id),
        "type": "access",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=settings.access_token_expires_seconds)).timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def create_2fa_pending_token(user_id: int) -> str:
    """Create a short-lived token that can ONLY be used for 2FA verification, not API access."""
    now = _utcnow()
    payload = {
        "sub": str(user_id),
        "type": "2fa_pending",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=300)).timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def create_refresh_token(user_id: int) -> str:
    """Create a longer-lived refresh token for the given user."""
    now = _utcnow()
    payload = {
        "sub": str(user_id),
        "type": "refresh",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=settings.refresh_token_expires_seconds)).timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def decode_token(token: str) -> dict:
    """Decode and verify a JWT, returning its payload."""
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError as exc:  # type: ignore[attr-defined]
        raise AuthenticationRequiredError("Invalid or expired token") from exc


async def _resolve_agent_screen_actor(device_token: str, screen_token: str) -> int | None:
    """Resolve a connector device token + screen token to the agent user acting.

    This is what makes ``一个 agent 就是一个 user`` reach the *whole* API: a call from
    inside an agent screen (``cheese api``/``cheese <verb>`` injects ``Authorization:
    Bearer <device token>`` + ``X-Cheese-Screen``) authorizes as that screen's agent
    user on every endpoint, exactly like a human's JWT — the token is necessary, not
    sufficient; each route still authorizes against the actor's real permissions.

    Only a call *from inside a screen* is accepted (a bare device token, which would
    act as the human owner, is not a general API credential). Lazy import avoids a
    circular dependency with the connector plane. Returns ``None`` on any failure so
    the caller falls through to the normal 401.
    """
    if not screen_token:
        return None
    try:
        from app.agent.attribution import resolve_actor
        from app.agent.connector_plane import device_service, hub

        attribution = await resolve_actor(
            device_service(), hub(), device_token=device_token, screen_token=screen_token
        )
    except Exception:  # auth fallback must never raise into the request
        return None
    if attribution is None or not attribution.inside_screen:
        return None
    return attribution.actor_user_id


async def get_current_user_id(
    x_user_id: Annotated[int | None, Header(alias="X-User-Id", convert_underscores=False)] = None,
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
    x_cheese_screen: Annotated[
        str | None, Header(alias="X-Cheese-Screen", convert_underscores=False)
    ] = None,
) -> int:
    """Resolve current user ID from Authorization bearer token or X-User-Id header.

    - Preferred: `Authorization: Bearer <accessToken>` issued by Python auth flow.
    - Agent: `Authorization: Bearer <device token>` + `X-Cheese-Screen` (a call from
      inside an agent screen) resolves to that agent user — same door as a human's.
    - Fallback: `X-User-Id` header (used in tests / transitional environments).
    """
    if authorization:
        token = authorization.strip()
        if token.lower().startswith("bearer "):
            token = token[7:].strip()
        # Try a human access token first; if it isn't a valid JWT, it may be a
        # connector device token from inside an agent screen.
        try:
            payload = decode_token(token)
        except AuthenticationRequiredError:
            agent_user_id = await _resolve_agent_screen_actor(token, x_cheese_screen or "")
            if agent_user_id is not None:
                return agent_user_id
            raise
        if payload.get("type") != "access":
            raise AuthenticationRequiredError("Invalid token type: expected access token")
        sub = payload.get("sub")
        try:
            return int(sub)
        except (TypeError, ValueError) as exc:
            raise AuthenticationRequiredError("Invalid token subject") from exc

    if x_user_id is not None:
        from app.core.config import settings

        if settings.environment not in ("development", "test"):
            raise AuthenticationRequiredError("X-User-Id header is not allowed in production")
        return x_user_id

    raise AuthenticationRequiredError("Authorization header is required")


async def get_optional_user_id(
    x_user_id: Annotated[int | None, Header(alias="X-User-Id", convert_underscores=False)] = None,
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> int | None:
    """Best-effort variant of get_current_user_id that returns None instead of 401.

    用于那些「可选」依赖当前用户上下文的查询（例如 task 列表的 joined 过滤），
    不强制要求调用方携带认证信息。
    """
    from app.core.errors import BaseError

    try:
        return await get_current_user_id(x_user_id=x_user_id, authorization=authorization)
    except (AuthenticationRequiredError, BaseError):
        return None
