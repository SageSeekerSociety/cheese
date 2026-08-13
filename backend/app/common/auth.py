import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, NamedTuple

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


def create_access_token(user_id: int, handle: str | None = None) -> str:
    """Create a short-lived access token for the given user.

    ``handle`` (= the user's username) is embedded as an extra claim so the ONE
    token also satisfies the cheesex auth layer, which keys on handle (fusion
    unify P3: one token for both API layers). Main auth reads ``sub`` (int id);
    cheesex reads ``handle`` (falling back to ``sub``)."""
    now = _utcnow()
    payload = {
        "sub": str(user_id),
        "type": "access",
        "iat": int(now.timestamp()),
        "exp": int(
            (now + timedelta(seconds=settings.access_token_expires_seconds)).timestamp()
        ),
    }
    if handle:
        payload["handle"] = handle
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


# Exported so the single-use reservation that retires a pending token expires
# with the token itself, instead of being kept in step by hand in two files.
PENDING_2FA_TTL_S = 300


class Minted2faPendingToken(NamedTuple):
    token: str
    # This ticket's one-shot identity. The signature only says the platform
    # minted it; only a live reservation says nobody has spent it yet — see
    # app.core.single_use_state and #222.
    jti: str


def mint_2fa_pending_token(
    user_id: int, *, expires_at: int | None = None
) -> Minted2faPendingToken:
    """A token that can ONLY complete 2FA verification, never access the API.

    Returns the ``jti`` alongside it, which the caller must reserve before
    handing the token out — kept out of here so this module stays pure JWT
    with no I/O, and so a caller cannot accidentally issue a ticket whose
    reservation failed (a ticket that can never be redeemed).

    ``expires_at`` (unix seconds) pins the new ticket to a deadline that
    already exists instead of starting a fresh 300s. Re-issuing after a wrong
    code passes the *original* ticket's ``exp`` here: otherwise a user — or
    an attacker — could keep the half-authenticated "password accepted, 2FA
    not yet" window alive forever just by continuing to guess wrong. The 2FA
    ceremony gets one deadline, however many codes are typed inside it.
    """
    now = int(_utcnow().timestamp())
    jti = uuid.uuid4().hex
    payload = {
        "sub": str(user_id),
        "type": "2fa_pending",
        "jti": jti,
        "iat": now,
        "exp": expires_at if expires_at is not None else now + PENDING_2FA_TTL_S,
    }
    return Minted2faPendingToken(
        token=jwt.encode(payload, settings.jwt_secret, algorithm="HS256"), jti=jti
    )


class Pending2faClaims(NamedTuple):
    user_id: int
    jti: str
    # The deadline of the whole 2FA ceremony, carried so a re-issued ticket
    # can inherit it rather than restart it.
    expires_at: int


def verify_2fa_pending_token(token: str) -> Pending2faClaims | None:
    """The claims a valid, unexpired 2FA ticket carries, else None.

    A ticket with no ``jti`` cannot be spent exactly once, so it is not a
    valid ticket — that includes any minted by the previous build. They are
    gone within the 300s TTL, and the login page already tells the user to
    sign in again.
    """
    try:
        decoded = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError:  # type: ignore[attr-defined]
        return None
    if decoded.get("type") != "2fa_pending":
        return None
    jti = decoded.get("jti")
    if not isinstance(jti, str) or not jti:
        return None
    try:
        user_id = int(decoded.get("sub") or "")
        expires_at = int(decoded["exp"])
    except (KeyError, TypeError, ValueError):
        return None
    return Pending2faClaims(user_id=user_id, jti=jti, expires_at=expires_at)


def create_refresh_token(user_id: int) -> str:
    """Create a longer-lived refresh token for the given user."""
    now = _utcnow()
    payload = {
        "sub": str(user_id),
        "type": "refresh",
        "iat": int(now.timestamp()),
        "exp": int(
            (
                now + timedelta(seconds=settings.refresh_token_expires_seconds)
            ).timestamp()
        ),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def decode_token(token: str) -> dict:
    """Decode and verify a JWT, returning its payload."""
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError as exc:  # type: ignore[attr-defined]
        raise AuthenticationRequiredError("Invalid or expired token") from exc


async def get_current_user_id(
    x_user_id: Annotated[
        int | None, Header(alias="X-User-Id", convert_underscores=False)
    ] = None,
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> int:
    """Resolve current user ID from Authorization bearer token or X-User-Id header.

    - Preferred: `Authorization: Bearer <accessToken>` issued by Python auth flow.
    - Fallback: `X-User-Id` header (used in tests / transitional environments).
    """
    if authorization:
        token = authorization.strip()
        if token.lower().startswith("bearer "):
            token = token[7:].strip()
        payload = decode_token(token)
        if payload.get("type") != "access":
            raise AuthenticationRequiredError(
                "Invalid token type: expected access token"
            )
        sub = payload.get("sub")
        if sub is None:
            raise AuthenticationRequiredError("Invalid token subject")
        try:
            return int(sub)
        except (TypeError, ValueError) as exc:
            raise AuthenticationRequiredError("Invalid token subject") from exc

    if x_user_id is not None:
        from app.core.config import settings

        if settings.environment not in ("development", "test"):
            raise AuthenticationRequiredError(
                "X-User-Id header is not allowed in production"
            )
        return x_user_id

    raise AuthenticationRequiredError("Authorization header is required")


async def get_optional_user_id(
    x_user_id: Annotated[
        int | None, Header(alias="X-User-Id", convert_underscores=False)
    ] = None,
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> int | None:
    """Best-effort variant of get_current_user_id that returns None instead of 401.

    用于那些「可选」依赖当前用户上下文的查询（例如 task 列表的 joined 过滤），
    不强制要求调用方携带认证信息。
    """
    from app.core.errors import BaseError

    try:
        return await get_current_user_id(
            x_user_id=x_user_id, authorization=authorization
        )
    except (AuthenticationRequiredError, BaseError):
        return None
