"""文档站访问记录:who came, once per visitor per UTC day.

The docs are static files; nothing on the server sees a page load. So the page
fires one small beacon (``docs/site/src/app.js``) and this module turns it into
a row — and, because the beacon is trivially replayable, into a row only once a
day. Three properties are the whole point, and each is enforced somewhere
specific:

* **One row per visitor per day.** The ``(day, visitor_id)`` unique constraint
  holds it (``models.DocsVisit``); the insert says so too
  (``ON CONFLICT DO NOTHING``) so that a repeat is a no-op rather than an error
  the caller has to handle.
* **Nothing that identifies a person beyond the account they are already signed
  into.** No IP address, no user agent, no page-by-page trail: the anonymous
  visitor is a random string the browser generates and keeps in localStorage,
  and the only page recorded is the one they arrived on. A signed-in reader is
  recorded by ``user_id`` — the platform already knows that mapping, so the row
  adds nothing about them that the account does not.
* **It cannot be hammered.** ``VisitLimits`` counts beacons per visitor per
  hour in Valkey, same shape as the ask limiter; past the cap the request is
  accepted and dropped (the visitor is, after all, reading the page — the
  answer to a flood is silence, not an error the page has to interpret).

Valkey down means **allowing**, not refusing — the opposite of the ask limiter,
and deliberately: a lost counter costs a few rows, while refusing would throw
away every visit for as long as the cache is down. The unique constraint still
holds the dedupe, so the worst a flood can do is keep one row per visitor per
day as it was going to be anyway.
"""

import logging
import re
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta

from redis.asyncio import Redis
from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.domain.docs_site.models import DocsVisit

logger = logging.getLogger(__name__)

# What the browser is allowed to send as its anonymous id: a random string it
# generated. Bounded and character-restricted so that a stranger cannot put a
# long or structured value into a column the report groups by.
_VISITOR = re.compile(r"^[A-Za-z0-9_-]{8,64}$")

# Beacons one visitor may send in an hour before the rest are dropped. Looser
# than the ask limiter by two orders of magnitude: a reader with several tabs
# open, reloading a page, is ordinary use; this only exists so that a script
# cannot turn the table into a write amplifier.
_PER_HOUR = 120
_WINDOW_S = 3600

# INCR and give the key its expiry the first time it is created.
_COUNT = """
local n = redis.call('INCR', KEYS[1])
if n == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end
return n
"""


def visitor_id(user_id: int | None, visitor: str | None) -> str | None:
    """The identity a visit dedupes on, or None when the beacon carries neither.

    A signed-in reader is the account (``u:<id>``) rather than the browser
    string, so signing in on a second browser does not count as a second
    visitor — the report asks 「有多少人来了」, and a person is one person.
    The anonymous id is refused unless it looks like one, so a malformed beacon
    is dropped rather than recorded under a key of its own choosing.
    """
    if user_id is not None:
        return f"u:{user_id}"
    if visitor and _VISITOR.match(visitor):
        return f"v:{visitor}"
    return None


def _utc_day(at: datetime | None = None) -> date:
    return (at or datetime.now(UTC)).date()


async def record(
    session: AsyncSession,
    *,
    visitor: str,
    user_id: int | None,
    page: str | None,
    at: datetime | None = None,
) -> bool:
    """Count one visit; ``False`` when this visitor was already counted today."""
    day = _utc_day(at)
    result = await session.execute(
        pg_insert(DocsVisit)
        .values(user_id=user_id, visitor_id=visitor, day=day, page=page)
        .on_conflict_do_nothing(constraint="uq_docs_visits_day_visitor")
    )
    await session.commit()
    # INSERT ... ON CONFLICT returns a CursorResult, which has rowcount at runtime.
    return bool(result.rowcount)  # type: ignore[attr-defined]


async def purge_old(session: AsyncSession) -> int:
    """Delete visits past the retention window; returns how many went."""
    cutoff = _utc_day() - timedelta(days=settings.docs_question_retention_days)
    result = await session.execute(delete(DocsVisit).where(DocsVisit.day < cutoff))
    await session.commit()
    # DELETE returns a CursorResult, which has rowcount at runtime.
    return int(result.rowcount or 0)  # type: ignore[attr-defined]


class VisitLimits:
    """How many beacons one visitor may send in an hour. Never refuses."""

    def __init__(self, redis: Callable[[], Redis | None]) -> None:
        self._client = redis

    async def admit(self, visitor: str) -> bool:
        redis = self._client()
        if redis is None:
            return True
        try:
            count = await redis.eval(  # type: ignore[misc]
                _COUNT, 1, f"docs-visit:h:{visitor}", str(_WINDOW_S)
            )
        except Exception:  # noqa: BLE001 — an unreachable counter allows
            logger.warning("docs visit limiter unavailable", exc_info=True)
            return True
        return int(count) <= _PER_HOUR
