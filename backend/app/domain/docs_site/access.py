"""Who is reading the docs: a sign-in of the docs' own, handed over by the platform.

The docs are static files on a host of their own (``site.origin()``), so they
never see the platform's sign-in, which is an access token in the app's
localStorage on another origin. The platform hands it over the way it does for
project sites and topic previews (``app.domain.site.hosting``):

1. On the platform, a signed-in browser asks for a grant (``mint_grant``): 30
   seconds, one use, naming the user and the sign-in session it came from.
2. It posts the grant to the docs host, which spends it (``spend_grant``) and
   answers with a cookie (``cookie_name``): HttpOnly, for the docs host alone
   (no Domain; ``__Host-`` makes the browser hold us to that), lasting
   ``docs_session_seconds``.
3. Every request that needs a reader — 问芝士, every file under dev/ — resolves
   the cookie (``reader``): signature, audience, the host it came in on, and the
   platform sign-in it was issued under, which must still be live. Signing out
   of the platform, or ending that device from the device list, ends the docs
   sign-in at the next request; nothing has to reach the docs host to do it.

Same-site, not same-origin: docs.okcheese.com and okcheese.com share a
registrable domain, so SameSite does not stop a page on another okcheese.com
host from sending the cookie along. What does: the grant must be posted from the
platform's origin, and a request that changes something on the docs host must
carry the docs' own Origin (``routes/docs_site.py``).

Admin status for dev/ is re-read at most once a minute (``admins``), so a
removed admin loses access within that minute, whatever the cookie says.

The backend's own reads behind the dev/ gate — the developer pages' index and
``.md`` twins, for agents in the platform's own project — carry an
``internal_pass``: a separate audience no browser is ever given.
"""

import hashlib
import hmac
import secrets
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass

import jwt
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.domain.docs_site import site
from app.domain.user.sessions import SessionService

GRANT_TTL = 30
GRANT = "docs-grant"
SESSION = "docs-session"
INTERNAL_AUDIENCE = "docs-dev-internal"
INTERNAL = "service:docs-index"
ADMIN_CACHE_SECONDS = 60


class GrantStoreUnavailable(Exception):
    """Valkey could not say whether a grant was already spent."""


@dataclass(frozen=True)
class Reader:
    user_id: int
    handle: str


def cookie_name() -> str:
    return (
        "__Host-cheese-docs"
        if site.origin().startswith("https://")
        else "cheese-docs-local"
    )


def cookie_secure() -> bool:
    return site.origin().startswith("https://")


def _key() -> bytes:
    return hmac.digest(
        settings.jwt_secret.encode(), b"cheese:docs-sign-in:v1", hashlib.sha256
    )


def _mint(user_id: int, sid: uuid.UUID, kind: str, ttl: int) -> str:
    now = int(time.time())
    return jwt.encode(
        {
            "sub": str(user_id),
            "sid": str(sid),
            "aud": site.origin(),
            "type": kind,
            "jti": secrets.token_urlsafe(16),
            "iat": now,
            "exp": now + ttl,
        },
        _key(),
        algorithm="HS256",
    )


def _claims(token: str | None, kind: str) -> tuple[int, uuid.UUID, str] | None:
    """(user id, sign-in session, token id) of a valid token of ``kind``."""
    if not token:
        return None
    try:
        claims = jwt.decode(
            token,
            _key(),
            algorithms=["HS256"],
            audience=site.origin(),
            options={"require": ["sub", "sid", "aud", "type", "jti", "iat", "exp"]},
        )
        if claims["type"] != kind or not str(claims["sub"]).isdigit():
            return None
        return int(claims["sub"]), uuid.UUID(str(claims["sid"])), str(claims["jti"])
    except (jwt.PyJWTError, ValueError, TypeError):
        return None


def mint_grant(user_id: int, sid: uuid.UUID) -> str:
    """What the platform hands a signed-in browser to carry to the docs host."""
    return _mint(user_id, sid, GRANT, GRANT_TTL)


async def spend_grant(
    token: str, redis: Callable[[], Redis | None]
) -> tuple[int, uuid.UUID] | None:
    """The sign-in a grant names, the first time it is presented; None for a
    grant that is forged, expired, for another host, or already spent.

    Raises ``GrantStoreUnavailable`` when Valkey cannot record the spend: a
    grant that might be replayed is refused, not waved through."""
    claims = _claims(token, GRANT)
    if claims is None:
        return None
    user_id, sid, jti = claims
    client = redis()
    if client is None:
        raise GrantStoreUnavailable
    try:
        # Kept past the grant's own expiry, so it cannot outlive its record.
        first = await client.set(f"docs-grant:{jti}", "1", nx=True, ex=GRANT_TTL * 2)
    except Exception as exc:  # noqa: BLE001 — an unreachable store refuses
        raise GrantStoreUnavailable from exc
    return (user_id, sid) if first else None


def mint_session(user_id: int, sid: uuid.UUID) -> str:
    return _mint(user_id, sid, SESSION, settings.docs_session_seconds)


async def reader(
    db: AsyncSession, cookie: str | None, host: str | None
) -> Reader | None:
    """Who holds this docs cookie, on this host, right now; None for anyone else.

    The cookie is only accepted on the docs' own host: the browser never sends
    it anywhere else, and a copy presented to the platform is refused rather
    than honoured."""
    if not site.on_docs_host(host):
        return None
    claims = _claims(cookie, SESSION)
    if claims is None:
        return None
    user_id, sid, _ = claims
    handle = await SessionService(db).live_handle(user_id, sid)
    return Reader(user_id, handle) if handle else None


def internal_pass() -> str:
    """A five-minute pass for the backend's own reads under dev/."""
    now = int(time.time())
    return jwt.encode(
        {"sub": INTERNAL, "aud": INTERNAL_AUDIENCE, "iat": now, "exp": now + 300},
        settings.jwt_secret,
        algorithm="HS256",
    )


def is_internal(token: str | None) -> bool:
    if not token:
        return False
    try:
        claims = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=["HS256"],
            audience=INTERNAL_AUDIENCE,
        )
    except jwt.PyJWTError:
        return False
    return claims.get("sub") == INTERNAL


class AdminSet:
    """The admin handles, re-read at most once a minute.

    The dev/ check runs on every file nginx serves under that path, so it must
    not cost a query each time; a minute is how long a removed admin keeps access.
    """

    def __init__(self) -> None:
        self._handles: frozenset[str] = frozenset()
        self._at = 0.0

    async def contains(self, handle: str, load) -> bool:
        if time.monotonic() - self._at > ADMIN_CACHE_SECONDS:
            self._handles = await load()
            self._at = time.monotonic()
        return handle in self._handles

    def forget(self) -> None:
        self._at = 0.0


admins = AdminSet()
