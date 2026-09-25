import uuid
from datetime import UTC, datetime, timedelta
from enum import StrEnum
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


def create_access_token(
    user_id: int | None, handle: str | None = None, *, sid: uuid.UUID | None = None
) -> str:
    """Create a short-lived access token for the given user.

    ``handle`` (= the user's username) is embedded as an extra claim so the ONE
    token also satisfies the 2.0 layer, which keys on handle. The numeric layer
    reads the user id from ``sub``. With no ``user_id`` the token names the
    handle alone: the handle-keyed layer accepts it and the numeric one treats
    its bearer as a guest.

    ``sid`` names the sign-in session the token was issued under, so a request
    can tell which session is its own. Nothing looks the session up per
    request (``app.domain.user.sessions``)."""
    if user_id is None and not handle:
        raise ValueError("an access token names a user id, a handle, or both")
    now = _utcnow()
    payload = {
        "sub": str(user_id) if user_id is not None else handle,
        "type": "access",
        "iat": int(now.timestamp()),
        "exp": int(
            (now + timedelta(seconds=settings.access_token_expires_seconds)).timestamp()
        ),
    }
    if handle:
        payload["handle"] = handle
    if sid is not None:
        payload["sid"] = str(sid)
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


# A sudo ticket outlives only the screen that asked for it: the client
# re-authenticates, gets the ticket, and immediately spends it on the one
# operation it was minted for.
SUDO_TICKET_TTL_S = 300


class SudoPurpose(StrEnum):
    """The one operation a sudo ticket may be spent on.

    A re-authentication is proof of presence for *something*. Without a name
    on it, a ticket the owner minted to add a passkey is equally good for
    switching their second factor off — the ceremony they consented to is not
    the one that gets performed. So the purpose is asked for at mint time,
    signed into the ticket, and checked at the entrance that redeems it.

    Only the operations whose gate the server actually enforces belong here:
    a credential with no lock to fit is not a protection, it is a spare key.
    """

    TWO_FA_ENABLE = "2fa:enable"
    TWO_FA_DISABLE = "2fa:disable"
    TWO_FA_BACKUP_CODES = "2fa:backup-codes"
    PASSKEY_ADD = "passkey:add"
    PASSKEY_DELETE = "passkey:delete"
    PASSWORD_CHANGE = "password:change"
    OAUTH_UNBIND = "oauth:unbind"
    REALNAME_VIEW = "realname:view"
    REALNAME_UPDATE = "realname:update"
    REALNAME_DELETE = "realname:delete"


class MintedSudoTicket(NamedTuple):
    token: str
    # Same split as the 2FA ticket above, for the same reason: the signature
    # says the platform minted it, and only a live reservation says nobody
    # has spent it yet.
    jti: str


def mint_sudo_ticket(user_id: int, purpose: SudoPurpose) -> MintedSudoTicket:
    """A ticket proving this user just re-authenticated, for ``purpose`` only.

    Like the 2FA ticket, the ``jti`` comes back for the caller to reserve —
    this module stays pure JWT with no I/O, and a ticket whose reservation
    failed must never reach a client, because nothing would redeem it.
    """
    now = int(_utcnow().timestamp())
    jti = uuid.uuid4().hex
    payload = {
        "sub": str(user_id),
        "type": "sudo",
        "purpose": purpose.value,
        "jti": jti,
        "iat": now,
        "exp": now + SUDO_TICKET_TTL_S,
    }
    return MintedSudoTicket(
        token=jwt.encode(payload, settings.jwt_secret, algorithm="HS256"), jti=jti
    )


class SudoTicketClaims(NamedTuple):
    user_id: int
    purpose: SudoPurpose
    jti: str


def verify_sudo_ticket(token: str) -> SudoTicketClaims | None:
    """The claims a valid, unexpired sudo ticket carries, else None.

    An access token is not a sudo ticket however freshly it was issued: the
    ``type`` check is what keeps "is signed in" from being read as "just
    proved they are here". A purpose this build does not know is likewise no
    ticket — an unrecognised name cannot be matched against the entrance
    redeeming it, so it can only be refused.
    """
    try:
        decoded = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError:  # type: ignore[attr-defined]
        return None
    if decoded.get("type") != "sudo":
        return None
    jti = decoded.get("jti")
    if not isinstance(jti, str) or not jti:
        return None
    try:
        user_id = int(decoded.get("sub") or "")
        purpose = SudoPurpose(decoded.get("purpose"))
    except (TypeError, ValueError):
        return None
    return SudoTicketClaims(user_id=user_id, purpose=purpose, jti=jti)


class AccessClaims(NamedTuple):
    # None when ``sub`` names a handle rather than a user id: such a token
    # authenticates to the handle-keyed layer only.
    user_id: int | None
    handle: str
    sid: uuid.UUID | None


def verify_access_token(token: str) -> AccessClaims | None:
    """What a valid, unexpired access token says, else None.

    The one check every access token goes through, whichever API layer it is
    presented to. It verifies the signature only: the session the token names
    is not consulted (``app.domain.user.sessions``).
    """
    if not token:
        return None
    try:
        decoded = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError:  # type: ignore[attr-defined]
        return None
    sub = decoded.get("sub")
    if decoded.get("type") != "access" or not isinstance(sub, str) or not sub:
        return None
    try:
        sid = uuid.UUID(decoded["sid"]) if decoded.get("sid") else None
    except (TypeError, ValueError):
        return None
    handle = decoded.get("handle")
    return AccessClaims(
        user_id=int(sub) if sub.isdigit() else None,
        handle=handle if isinstance(handle, str) and handle else sub,
        sid=sid,
    )


def _bearer_claims(authorization: str | None) -> AccessClaims | None:
    token = (authorization or "").strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    return verify_access_token(token)


async def get_current_user_id(
    x_user_id: Annotated[
        int | None, Header(alias="X-User-Id", convert_underscores=False)
    ] = None,
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> int:
    """Resolve current user ID from Authorization bearer token or X-User-Id header.

    - Preferred: `Authorization: Bearer <accessToken>` issued by Python auth flow.
    - Fallback: `X-User-Id` header, a local-development convenience. Refused on
      any deployment: one started by the deploy compose file, whatever
      `environment` reads, and any whose `environment` is not development/test.
    """
    if authorization:
        claims = _bearer_claims(authorization)
        if claims is None or claims.user_id is None:
            raise AuthenticationRequiredError("Invalid or expired token")
        return claims.user_id

    if x_user_id is not None:
        from app.core.config import settings

        if settings.deployed_via_compose or settings.environment not in (
            "development",
            "test",
        ):
            raise AuthenticationRequiredError(
                "X-User-Id header is not allowed on a deployment"
            )
        return x_user_id

    raise AuthenticationRequiredError("Authorization header is required")


async def get_current_session_id(
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> uuid.UUID | None:
    """The sign-in session the caller's access token was issued under."""
    claims = _bearer_claims(authorization)
    return claims.sid if claims is not None else None


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
