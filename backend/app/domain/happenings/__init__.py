"""Something happened that a rule may be waiting for: say so, once.

A person's 芝士 can be asked to act when something happens — someone
@-mentions them, a task goes to review, a review is decided. The places those
things happen call ``happened`` in the transaction that makes them happen, and
know nothing more: not who is waiting, nor what they will do. What waits reads
the rows (``taken``) and deletes them as it reads.

The row commits with the thing itself, so a thing that happened is never
missed and one rolled back was never said. Once the transaction commits, the
reader is woken through Valkey (``WAKE_KEY``) rather than finding the row on
its clock; the clock is only for a wake that was lost.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable

from redis.asyncio import Redis
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.background import spawn
from app.core.db import after_own_commit
from app.core.redis import get_redis_client
from app.domain.happenings.models import Happening

logger = logging.getLogger(__name__)

#: A person was sent a notification: ``payload`` holds its ``type`` and the
#: notification's own ``payload``.
NOTIFIED = "notified"
#: A task went to review: ``payload`` holds ``title``, ``topicId``,
#: ``reporter`` and ``reviewers`` (handles).
TASK_FILED = "task_filed"
#: A task's review was decided: as ``TASK_FILED``, and ``outcome``,
#: ``accepted`` or ``rejected``.
TASK_DECIDED = "task_decided"
KINDS = frozenset({NOTIFIED, TASK_FILED, TASK_DECIDED})

#: The Valkey list a commit that said something pushes to.
WAKE_KEY = "cheese:happenings:wake"


async def happened(
    session: AsyncSession,
    kind: str,
    *,
    payload: dict,
    user_id: int | None = None,
    task_id: uuid.UUID | None = None,
    project_id: uuid.UUID | None = None,
) -> None:
    """Say that ``kind`` happened, in the caller's transaction."""
    if kind not in KINDS:  # pragma: no cover - a miswired caller
        # i18n-exempt: developer miswiring, not a refusal a reader can act on
        raise ValueError(f"unknown happening {kind!r}")
    session.add(
        Happening(
            kind=kind,
            user_id=user_id,
            task_id=task_id,
            project_id=project_id,
            payload=payload,
        )
    )
    after_own_commit(session, _wake)


def _wake() -> None:
    redis = get_redis_client()
    if redis is not None:
        spawn(_push(redis), name="happenings wake")


async def _push(redis: Redis) -> None:
    await redis.lpush(WAKE_KEY, b"1")


async def taken(session: AsyncSession, limit: int) -> list[Happening]:
    """The oldest happenings no other reader holds, deleted as they are read:
    the caller's commit takes them for good, its rollback gives them back."""
    rows = list(
        await session.scalars(
            select(Happening)
            .order_by(Happening.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    )
    if rows:
        await session.execute(
            delete(Happening).where(Happening.id.in_([r.id for r in rows]))
        )
    return rows


def waiter(redis: Callable[[], Redis | None]) -> Callable[[float], Awaitable[bool]]:
    """Wait up to that many seconds for a wake: whether one came before they
    were up. Wakes that came together are taken as one."""

    async def wait(seconds: float) -> bool:
        client = redis()
        if client is None or seconds < 1:
            await asyncio.sleep(max(0.0, seconds))
            return False
        try:
            popped = await client.blpop([WAKE_KEY], timeout=int(seconds))
            if popped is None:
                return False
            await client.delete(WAKE_KEY)
        except Exception:  # Valkey away: the clock still comes round
            logger.warning("waiting for a happening failed", exc_info=True)
            await asyncio.sleep(seconds)
            return False
        return True

    return wait
