"""At-least-once document refresh hints; GET remains the snapshot authority.

A crash between publish and commit may repeat a hint. The persisted version
cursor survives dispatch and lets a reconnecting client recover missed hints
without replaying active turns. Rows are marked only after publish succeeds.
"""

import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.living_doc.models import DocumentRefresh
from app.domain.living_doc.services import refresh_hint


async def dispatch_pending(
    session: AsyncSession,
    publish: Callable[[str, dict], Awaitable[None]],
    room_id: uuid.UUID | None = None,
) -> int:
    query = (
        select(DocumentRefresh)
        .where(DocumentRefresh.dispatched_at.is_(None))
        .order_by(DocumentRefresh.created_at, DocumentRefresh.version)
        .limit(100)
        .with_for_update(skip_locked=True)
    )
    if room_id is not None:
        query = query.where(DocumentRefresh.room_id == room_id)
    rows = list(await session.scalars(query))
    for row in rows:
        await publish(str(row.room_id), refresh_hint(row))
        row.dispatched_at = datetime.now(UTC)
    await session.commit()
    return len(rows)


async def drain_refreshes(sessions, publish) -> int:
    async with sessions() as session:
        return await dispatch_pending(session, publish)
