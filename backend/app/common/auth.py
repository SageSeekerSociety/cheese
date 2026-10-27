import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, NamedTuple

from fastapi import Header

from app.core import credentials
from app.core.config import settings
from app.core.credentials import Kind


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

    ``handle`` (= the user's username) is embedded as an extra claim: every
    authorization key is the handle, and a token that states it is named by it
    (`app.auth.caller.handle_named_by`). ``sub`` carries the user id. With no
    ``user_id`` the token names the handle alone, and a request resolves the
    user id from that handle (`ActorResolver.person`).

    ``sid`` names the sign-in session the token was issued under, so a request
    can tell which session is its own. Nothing looks the session up per
    request (``app.domain.user.sessions``)."""
    if user_id is None and not handle:
        raise ValueError("an access token names a user id, a handle, or both")
    payload = {"sub": str(user_id) if user_id is not None else handle}
    if handle:
        payload["handle"] = handle
    if sid is not None:
        payload["sid"] = str(sid)
    return credentials.mint(
        Kind.ACCESS, payload, ttl_s=settings.access_token_expires_seconds
    )


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
        "jti": jti,
        "exp": expires_at if expires_at is not None else now + PENDING_2FA_TTL_S,
    }
    return Minted2faPendingToken(
        token=credentials.mint(Kind.TWO_FA_PENDING, payload), jti=jti
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
    decoded = credentials.read(token, Kind.TWO_FA_PENDING)
    if decoded is None:
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
    ACCOUNT_DELETE = "account:delete"


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
    jti = uuid.uuid4().hex
    payload = {"sub": str(user_id), "purpose": purpose.value, "jti": jti}
    return MintedSudoTicket(
        token=credentials.mint(Kind.SUDO, payload, ttl_s=SUDO_TICKET_TTL_S), jti=jti
    )


class SudoTicketClaims(NamedTuple):
    user_id: int
    purpose: SudoPurpose
    jti: str


def verify_sudo_ticket(token: str) -> SudoTicketClaims | None:
    """The claims a valid, unexpired sudo ticket carries, else None.

    An access token is not a sudo ticket however freshly it was issued: the
    kind check is what keeps "is signed in" from being read as "just
    proved they are here". A purpose this build does not know is likewise no
    ticket — an unrecognised name cannot be matched against the entrance
    redeeming it, so it can only be refused.
    """
    decoded = credentials.read(token, Kind.SUDO)
    if decoded is None:
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
    # Whether ``handle`` is a name the token itself states (its ``handle``
    # claim) or is the ``sub`` claim standing in for a missing one. Nothing in
    # the value tells them apart: ``sub`` is the int user PK whenever the token
    # carries one, and a username may be all digits too
    # (``app.domain.user.services._USERNAME_RE`` takes ``2026201782``), so a
    # handle-keyed check that reads the standing-in value as a name hands that
    # caller a handle matching no roster — every permission they hold, revoked
    # in silence.
    handle_from_claim: bool
    sid: uuid.UUID | None
    # When the token was issued (``iat``): a closed account refuses the tokens
    # issued before it closed (``app.core.deleted_accounts``).
    issued_at: int | None = None
    # When the token was issued (``iat``): a closed account refuses the tokens
    # issued before it closed (``app.core.deleted_accounts``).
    issued_at: int | None = None


def create_notice_token(user_id: int, sid: uuid.UUID) -> str:
    """The credential the desktop app keeps to hear about one person's notices.

    It does nothing else: only the live notices connection
    (``app/api/routes/notifications_live.py``) accepts it, and every other route
    takes an access token. It carries no expiry of its own; it is good for
    as long as the sign-in session ``sid`` is, which that connection checks.
    """
    return credentials.mint(Kind.NOTICES, {"sub": str(user_id), "sid": str(sid)})


def verify_notice_token(token: str) -> tuple[int, uuid.UUID] | None:
    """(user id, session id) a notices credential names, if its signature holds."""
    decoded = credentials.read(token, Kind.NOTICES)
    if decoded is None:
        return None
    sub = decoded.get("sub")
    if not isinstance(sub, str) or not sub.isdigit():
        return None
    try:
        return int(sub), uuid.UUID(decoded["sid"])
    except (KeyError, TypeError, ValueError):
        return None


class AppSignInClaims(NamedTuple):
    user_id: int
    challenge: str
    jti: str


APP_SIGN_IN_TTL_S = 5 * 60


def create_app_sign_in_code(user_id: int, challenge: str) -> str:
    """A sign-in made in the browser, for the desktop app to take over.

    Good once (the ``jti`` is reserved by the caller), for a few minutes, and
    only to whoever holds the verifier ``challenge`` is the hash of: the code
    travels through a ``cheese://`` link, which any program on the computer
    could have registered for.
    """
    payload = {"sub": str(user_id), "chl": challenge, "jti": uuid.uuid4().hex}
    return credentials.mint(Kind.APP_SIGN_IN, payload, ttl_s=APP_SIGN_IN_TTL_S)


def verify_app_sign_in_code(code: str) -> AppSignInClaims | None:
    decoded = credentials.read(code, Kind.APP_SIGN_IN)
    if decoded is None:
        return None
    sub = decoded.get("sub")
    challenge, jti = decoded.get("chl"), decoded.get("jti")
    if (
        not isinstance(sub, str)
        or not sub.isdigit()
        or not isinstance(challenge, str)
        or not isinstance(jti, str)
        or not jti
    ):
        return None
    return AppSignInClaims(int(sub), challenge, jti)


def verify_access_token(token: str) -> AccessClaims | None:
    """What a valid, unexpired access token says, else None.

    The one check every access token goes through, whichever API layer it is
    presented to. It verifies the signature only: the session the token names
    is not consulted (``app.domain.user.sessions``).
    """
    decoded = credentials.read(token, Kind.ACCESS)
    if decoded is None:
        return None
    sub = decoded.get("sub")
    if not isinstance(sub, str) or not sub:
        return None
    try:
        sid = uuid.UUID(decoded["sid"]) if decoded.get("sid") else None
    except (TypeError, ValueError):
        return None
    stated = decoded.get("handle")
    if isinstance(stated, str) and stated:
        name, handle_from_claim = stated, True
    else:
        name, handle_from_claim = sub, False
    iat = decoded.get("iat")
    return AccessClaims(
        user_id=int(sub) if sub.isdigit() else None,
        handle=name,
        handle_from_claim=handle_from_claim,
        sid=sid,
        issued_at=iat if isinstance(iat, int) else None,
    )


def _bearer_claims(authorization: str | None) -> AccessClaims | None:
    token = (authorization or "").strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    return verify_access_token(token)


async def get_current_session_id(
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> uuid.UUID | None:
    """The sign-in session the caller's access token was issued under."""
    claims = _bearer_claims(authorization)
    return claims.sid if claims is not None else None
