"""Who may read /docs/dev/: a short-lived pass for platform admins.

The docs site is static files behind nginx, which cannot read the access token
the app keeps in localStorage. So an admin's browser trades that token for a
cookie once (``issue``), scoped to ``/docs/dev`` and nothing else, and nginx's
``auth_request`` asks ``verify`` before serving every file under that path —
pages, search index, .md twins, diagrams alike.

``verify`` re-checks that the holder is still an admin, so removing someone
from the admin list closes the door within ``ADMIN_CACHE_SECONDS`` rather than
when their pass expires.
"""

import time
from datetime import UTC, datetime, timedelta

import jwt

from app.core.config import settings

COOKIE = "cheese_docs_dev"
COOKIE_PATH = "/docs/dev"
AUDIENCE = "docs-dev"
ADMIN_CACHE_SECONDS = 60


def issue(handle: str, now: datetime | None = None) -> tuple[str, int]:
    """A signed pass for ``handle`` and its lifetime in seconds."""
    now = now or datetime.now(UTC)
    ttl = settings.docs_dev_session_seconds
    token = jwt.encode(
        {
            "sub": handle,
            "aud": AUDIENCE,
            "iat": now,
            "exp": now + timedelta(seconds=ttl),
        },
        settings.jwt_secret,
        algorithm="HS256",
    )
    return token, ttl


def holder(token: str | None) -> str | None:
    """The handle a valid, unexpired pass was issued to; None for anything else."""
    if not token:
        return None
    try:
        claims = jwt.decode(
            token, settings.jwt_secret, algorithms=["HS256"], audience=AUDIENCE
        )
    except jwt.PyJWTError:
        return None
    sub = claims.get("sub")
    return sub if isinstance(sub, str) and sub else None


class AdminSet:
    """The admin handles, re-read at most once a minute.

    ``verify`` runs on every file nginx serves under /docs/dev/, so it must not
    cost a query each time; a minute is how long a removed admin keeps access.
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
