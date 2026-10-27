"""How long a session's raw records keep their database row.

The transcripts themselves are kept for good, by a decision to keep
transcripts permanently (#4398). Nothing here, or anywhere else, deletes a
sealed run's object from the private bucket.

What goes is the row. A sealed run's row is deleted `RETENTION` (30 days)
after its last record, so the database holds an index of the last month;
past that `read` finds the run through the bucket's permanent index
(`service.read`). A run still held in
its row (no private bucket configured, so never sealed) is not expired: the
row is its only copy.

A conversation that is gone loses its rows the same way, objects kept:
`forget_gone` finds the rows of conversations that no longer exist, or belong
to a channel a person deleted (the channel's own, its tasks', its 支线'). Where
there is a bucket it takes only sealed rows: a run still in its row is sealed
first, on the sealer's clock, and its row goes on a later pass.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import (
    DateTime,
    Uuid,
    column,
    delete,
    exists,
    not_,
    or_,
    table,
)

from app.core.db import SessionFactory
from app.domain.conversation.models import Conversation
from app.domain.session_record import service
from app.domain.session_record.models import RETENTION, SessionRecord

logger = logging.getLogger(__name__)

#: How often the retention sweep looks.
EVERY_S = 600

# The columns this reads of tables in packages above this one, declared rather
# than imported, as `machine.admin` does: which channels a person deleted, and
# which tasks and 支线 are in a channel.
_rooms = table(
    "topics", column("id", Uuid), column("deleted_at", DateTime(timezone=True))
)
_tasks = table("tasks", column("id", Uuid), column("room_id", Uuid))
_threads = table("threads", column("id", Uuid), column("room_id", Uuid))


async def sweep(sessions: SessionFactory) -> dict[str, int]:
    """The periodic job: forget gone conversations, then expire old rows. Each
    half runs whatever happened to the other."""
    forgotten = 0
    try:
        forgotten = await forget_gone(sessions)
    except Exception:
        logger.exception("forgetting gone conversations' session records failed")
    expired = await expire(sessions)
    return {"expired": expired, "forgotten": forgotten}


async def expire(sessions: SessionFactory, *, now: datetime | None = None) -> int:
    """Delete the rows of sealed runs older than `RETENTION`; how many.

    Only the row: the run's object stays in the bucket for good (the decision
    above), so no storage call is made here.
    """
    cutoff = (now or datetime.now(UTC)) - RETENTION
    async with sessions() as db:
        result = await db.execute(
            delete(SessionRecord)
            .where(SessionRecord.object_key.is_not(None))
            .where(SessionRecord.last_at < cutoff)
        )
        await db.commit()
    # A DELETE's result is a CursorResult; `execute` is typed as returning Result.
    return result.rowcount or 0  # type: ignore[attr-defined]


async def forget_gone(sessions: SessionFactory) -> int:
    """Delete the rows of conversations that are gone; how many rows.

    Rows only, never objects: a deleted conversation's transcript stays in the
    bucket, by a decision to keep transcripts permanently (#4398); what goes
    is the app's way to read it. With a bucket, a row not yet sealed is left
    for the sealer, so the conversation's last minutes, and whatever its
    sessions write after it was deleted, reach the bucket too. Without one
    the row is the only copy there is, and goes.
    """
    deleted_room = exists().where(
        _rooms.c.id == SessionRecord.conversation_id, _rooms.c.deleted_at.is_not(None)
    )
    in_deleted_room = [
        exists().where(
            part.c.id == SessionRecord.conversation_id,
            exists().where(
                _rooms.c.id == part.c.room_id, _rooms.c.deleted_at.is_not(None)
            ),
        )
        for part in (_tasks, _threads)
    ]
    query = delete(SessionRecord)
    if service.bucket() is not None:
        query = query.where(SessionRecord.object_key.is_not(None))
    async with sessions() as db:
        result = await db.execute(
            query.where(
                or_(
                    not_(
                        exists().where(Conversation.id == SessionRecord.conversation_id)
                    ),
                    # A deleted channel keeps its row until its cleanup is
                    # done; what was said in it, and in its tasks and 支线, is
                    # to stop being readable once it is deleted
                    # (`TopicService.delete`).
                    deleted_room,
                    *in_deleted_room,
                )
            )
        )
        await db.commit()
    return result.rowcount or 0  # type: ignore[attr-defined]
