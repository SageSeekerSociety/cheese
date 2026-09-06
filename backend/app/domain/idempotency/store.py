"""Claim a side effect exactly once, durably.

``claim`` is an atomic test-and-set on the ``idempotency_keys`` table:

    if not await claim(session, key, action="split", scope_id=str(topic_id)):
        return <the first execution's result>
    ...do the side effect...
    await record_result(session, key, result)

Both statements run on the CALLER's session, so the key and the effect commit
together. That is the property Redis cannot give us here: a Redis SETNX plus a
DB write are two commits with a hole between them, and a re-sent turn (重发) is
precisely a mechanism for arriving in that hole.

``INSERT ... ON CONFLICT DO NOTHING RETURNING id`` rather than SELECT-then-INSERT:
the latter races two concurrent claims into both believing they won, and it also
poisons the transaction with an IntegrityError when they collide.
"""

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.idempotency.models import IdempotencyKey

logger = logging.getLogger(__name__)


async def claim(session: AsyncSession, key: str, *, action: str, scope_id: str) -> bool:
    """True when THIS caller owns the effect and must perform it; False when it
    already happened (or is happening) and the caller must not repeat it."""
    stmt = (
        pg_insert(IdempotencyKey)
        .values(key=key, action=action, scope_id=scope_id[:64])
        .on_conflict_do_nothing(index_elements=["key"])
        .returning(IdempotencyKey.id)
    )
    won = (await session.execute(stmt)).scalar_one_or_none() is not None
    if not won:
        logger.info("idempotent skip: action=%s scope=%s", action, scope_id)
    return won


async def record_result(
    session: AsyncSession, key: str, result: dict[str, Any]
) -> None:
    """Attach the winner's output to its key so a losing replay can return the
    SAME answer instead of an error. Best-effort by design: a missing result
    degrades a replay to "already done, nothing returned", never to a repeat."""
    row = (
        await session.execute(select(IdempotencyKey).where(IdempotencyKey.key == key))
    ).scalar_one_or_none()
    if row is not None:
        row.result = result
        await session.flush()


async def stored_result(session: AsyncSession, key: str) -> dict[str, Any] | None:
    """The first execution's recorded output, if it got as far as writing one."""
    row = (
        await session.execute(select(IdempotencyKey).where(IdempotencyKey.key == key))
    ).scalar_one_or_none()
    return row.result if row is not None else None


__all__ = ["claim", "record_result", "stored_result"]
