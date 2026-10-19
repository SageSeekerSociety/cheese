"""How long a session's raw records are kept, and what is kept after that.

Session records are kept for `RETENTION` (30 days, the span #1611 set for a
session's transcripts). Past that, a sealed run's object and row go, and the
inputs the session was sent — what a person or the platform said into it —
are kept: they are written as a run of their own, gzip JSONL under
`archive/user-messages/<conversation>/`, read back to check it landed, and
only then is the old run removed. That archive run is kept as long as its
conversation is.

A conversation that is gone takes everything with it, the archive included.
`session_records` has no foreign key to `conversations` for exactly that
reason: a cascade would delete the rows and leave their objects in the bucket
with nothing left that names them. `forget_gone` finds the rows of
conversations that no longer exist, or belong to a channel a person deleted
(the channel's own, its tasks', its 支线'), and deletes them and their objects.

Records still in their rows (no private bucket configured, so never sealed)
are not expired: the archive needs the bucket. A gone conversation's rows go
with or without one.
"""

from __future__ import annotations

import io
import logging
import uuid
from collections.abc import Set as AbstractSet
from datetime import UTC, datetime, timedelta

from sqlalchemy import (
    DateTime,
    Uuid,
    column,
    delete,
    distinct,
    exists,
    not_,
    or_,
    select,
    table,
)

from app.core.db import SessionFactory
from app.core.storage import StorageBackend
from app.domain.conversation.models import Conversation
from app.domain.session_record import service
from app.domain.session_record.models import SessionRecord

logger = logging.getLogger(__name__)

#: How long a session's records are kept whole.
RETENTION = timedelta(days=30)
#: How often the retention sweep looks.
EVERY_S = 600
#: Where the inputs of expired runs are kept, per conversation.
ARCHIVE_PREFIX = "archive/user-messages"
#: Runs expired, and gone conversations forgotten, per pass.
PER_PASS = 200

# The columns this reads of tables in packages above this one, declared rather
# than imported, as `machine.admin` does: which channels a person deleted, and
# which tasks and 支线 are in a channel.
_rooms = table(
    "topics", column("id", Uuid), column("deleted_at", DateTime(timezone=True))
)
_tasks = table("tasks", column("id", Uuid), column("room_id", Uuid))
_threads = table("threads", column("id", Uuid), column("room_id", Uuid))


def is_input(record: dict, children: AbstractSet[str] = frozenset()) -> bool:
    """Whether this record is an input the session was sent.

    Claude Code echoes every input it reads (`--replay-user-messages`):
    the replay is the input, while the other user records carry tool results.
    Codex reports each input as a `userMessage` item, and pi as a user message
    entry. A prompt the agent itself gave a subagent is not an input: pi marks
    those entries, and for Codex `children` names the subagents' threads.
    """
    if record.get("type") == "user":
        return record.get("isReplay") is True
    if record.get("method") == "item/completed":
        params = record.get("params") or {}
        item = params.get("item") or {}
        return (
            item.get("type") == "userMessage" and params.get("threadId") not in children
        )
    message = record.get("message")
    return (
        record.get("type") == "message"
        and isinstance(message, dict)
        and message.get("role") == "user"
        and not record.get("subagent")
    )


def _children(entries: list[dict]) -> set[str]:
    """The Codex threads a run's records say are subagents' (`thread/started`
    with a parent)."""
    found: set[str] = set()
    for entry in entries:
        record = entry["record"]
        if record.get("method") == "thread/started":
            thread = (record.get("params") or {}).get("thread") or {}
            if thread.get("parentThreadId") and thread.get("id"):
                found.add(str(thread["id"]))
    return found


async def sweep(sessions: SessionFactory) -> dict[str, int]:
    """The periodic job: forget gone conversations, then expire old runs."""
    storage = service.bucket()
    forgotten = await forget_gone(sessions, storage)
    expired = 0 if storage is None else await expire(sessions, storage)
    return {"expired": expired, "forgotten": forgotten}


async def expire(
    sessions: SessionFactory,
    storage: StorageBackend,
    *,
    now: datetime | None = None,
) -> int:
    """Expire sealed runs older than `RETENTION`, keeping their inputs; how many.

    A run that cannot be expired (its object unreadable) is logged and left
    for the next pass; the runs behind it are not held up by it.
    """
    cutoff = (now or datetime.now(UTC)) - RETENTION
    expired = 0
    failed: set[uuid.UUID] = set()
    while expired + len(failed) < PER_PASS:
        outcome = await _expire_one(sessions, storage, cutoff, failed)
        if outcome is None:
            break
        if outcome is True:
            expired += 1
    return expired


async def _expire_one(
    sessions: SessionFactory,
    storage: StorageBackend,
    cutoff: datetime,
    failed: set[uuid.UUID],
) -> bool | None:
    """Expire the oldest run due; None when none is, False when it failed."""
    async with sessions() as db:
        query = (
            select(SessionRecord)
            .where(SessionRecord.object_key.is_not(None))
            .where(SessionRecord.object_key.not_like(f"{ARCHIVE_PREFIX}/%"))
            .where(SessionRecord.last_at < cutoff)
            .order_by(SessionRecord.last_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if failed:
            query = query.where(SessionRecord.id.not_in(failed))
        run = (await db.execute(query)).scalar_one_or_none()
        if run is None:
            return None
        assert run.object_key is not None
        try:
            await _replace_with_inputs(db, storage, run)
        except Exception:
            logger.exception("could not expire session record run %s", run.id)
            await db.rollback()
            failed.add(run.id)
            return False
    return True


async def _replace_with_inputs(db, storage: StorageBackend, run: SessionRecord) -> None:
    """Write the run's inputs as a run of their own, then remove the run, its
    object last before the commit: a failure before then leaves the run whole."""
    assert run.object_key is not None
    body = await storage.download(run.object_key)
    # An object already gone (a pass that deleted it and then failed to
    # commit) has nothing left to keep: the row goes on its own.
    entries = [] if body is None else service.unpack(body)
    children = _children(entries)
    entries = [e for e in entries if is_input(e["record"], children)]
    archive: SessionRecord | None = None
    archive_key: str | None = None
    if entries:
        packed = await service.pack(entries)
        archive_key = (
            f"{ARCHIVE_PREFIX}/{run.conversation_id}/{uuid.uuid4().hex}.jsonl.gz"
        )
        await storage.upload(io.BytesIO(packed), archive_key, "application/gzip")
        # Read back before anything is deleted: the inputs are the one thing
        # kept past the retention, so they must be there first.
        if await storage.download(archive_key) != packed:
            # i18n-exempt: a storage fault for the logs; the run is kept
            raise RuntimeError(f"archived inputs did not read back: {archive_key}")
        first, last = entries[0], entries[-1]
        archive = SessionRecord(
            conversation_id=run.conversation_id,
            turn_id=run.turn_id,
            agent_handle=run.agent_handle,
            harness=run.harness,
            first_sequence=int(first["sequence"]),
            last_sequence=int(last["sequence"]),
            first_at=datetime.fromisoformat(first["at"]),
            last_at=datetime.fromisoformat(last["at"]),
            count=len(entries),
            bytes=sum(service.size(e) for e in entries),
            records=None,
            object_key=archive_key,
        )
    try:
        await db.execute(delete(SessionRecord).where(SessionRecord.id == run.id))
        if archive is not None:
            db.add(archive)
        await db.flush()
        if body is not None:
            await storage.delete(run.object_key)
        await db.commit()
    except Exception:
        # The archive object would otherwise outlive a run that was kept.
        if archive_key is not None:
            await storage.delete(archive_key)
        raise


async def forget_gone(sessions: SessionFactory, storage: StorageBackend | None) -> int:
    """Delete the records, objects and archive of conversations that are gone;
    how many conversations."""
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
    async with sessions() as db:
        gone = (
            (
                await db.execute(
                    select(distinct(SessionRecord.conversation_id))
                    .where(
                        or_(
                            not_(
                                exists().where(
                                    Conversation.id == SessionRecord.conversation_id
                                )
                            ),
                            # A deleted channel keeps its row until its cleanup
                            # is done; what was said in it, and in its tasks and
                            # 支线, is gone from the moment it is deleted
                            # (`TopicService.delete`).
                            deleted_room,
                            *in_deleted_room,
                        )
                    )
                    .limit(PER_PASS)
                )
            )
            .scalars()
            .all()
        )
    for conversation_id in gone:
        async with sessions() as db:
            # The rows this deletes are exactly the ones whose objects go: a
            # run the sealer commits meanwhile is not among them, and is found
            # on the next pass with its object.
            keys = (
                (
                    await db.execute(
                        delete(SessionRecord)
                        .where(SessionRecord.conversation_id == conversation_id)
                        .returning(SessionRecord.object_key)
                    )
                )
                .scalars()
                .all()
            )
            # Objects before the commit: if one cannot be deleted, the rows
            # come back and the next pass tries again.
            if storage is not None:
                for key in filter(None, keys):
                    await storage.delete(key)
            await db.commit()
    return len(gone)
