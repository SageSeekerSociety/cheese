"""Archives of sandbox homes kept for a while, then deleted.

Idle sandboxes used to be archived to the private bucket; now they are
destroyed (``lifecycle``). The archives written before that are kept until
each one's ``delete_after`` so that a person can fetch one by hand
(``scripts/retained_files.py``). Each conversation that has one is told once
until when, and on how to get it; past that date the object and its row go.

The sandbox sweep runs this (``runner.SandboxSweeper``).
"""

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.sentences import say
from app.core.storage import private_storage
from app.domain.machine.models import RetainedHomeArchive
from app.domain.machine.progress import publish_line, say_in

logger = logging.getLogger("cheese.machine.retained_archives")

#: Conversations told, and archives deleted, per sweep.
PER_SWEEP = 20
#: The date in a notice is the day in this offset: the platform's people are
#: in China.
DAY_OFFSET = timedelta(hours=8)


async def sweep(session: AsyncSession) -> dict[str, int]:
    return {"told": await tell(session), "expired": await expire(session)}


async def tell(session: AsyncSession) -> int:
    """Tell each conversation that has an archive no one told it about, once,
    until when the archives are kept."""
    rows = (
        await session.execute(
            select(
                RetainedHomeArchive.conversation_id,
                RetainedHomeArchive.delete_after,
            )
            .where(RetainedHomeArchive.told_at.is_(None))
            .order_by(RetainedHomeArchive.delete_after)
        )
    ).all()
    until: dict = {}
    for conversation_id, delete_after in rows:
        until.setdefault(conversation_id, delete_after)
    told = 0
    for conversation_id, delete_after in list(until.items())[:PER_SWEEP]:
        day = (delete_after + DAY_OFFSET).date().isoformat()
        line = await say_in(
            session,
            conversation_id,
            say("sandboxArchivesKept", date=day),
            {"event_type": "sandbox_archives_kept"},
        )
        await session.execute(
            update(RetainedHomeArchive)
            .where(RetainedHomeArchive.conversation_id == conversation_id)
            .values(told_at=datetime.now(UTC))
        )
        await session.commit()
        await publish_line(conversation_id, line)
        told += 1
    return told


async def expire(session: AsyncSession) -> int:
    """Delete the archives past their date, object first."""
    due = list(
        await session.scalars(
            select(RetainedHomeArchive)
            .where(RetainedHomeArchive.delete_after < datetime.now(UTC))
            .limit(PER_SWEEP)
        )
    )
    if not due:
        return 0
    bucket = private_storage()
    for archive in due:
        await bucket.delete(archive.key)
        await session.delete(archive)
        await session.commit()
        logger.info("deleted kept sandbox archive %s", archive.key)
    return len(due)
