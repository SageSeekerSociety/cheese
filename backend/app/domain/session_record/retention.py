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
    text,
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
    """The periodic job: forget gone conversations, then expire old runs. Each
    half runs whatever happened to the other."""
    storage = service.bucket()
    forgotten = expired = 0
    try:
        forgotten = await forget_gone(sessions, storage)
    except Exception:
        logger.exception("forgetting gone conversations' session records failed")
    if storage is not None:
        expired = await expire(sessions, storage)
    return {"expired": expired, "forgotten": forgotten}


#: Sealed runs that are not archives: what expiry walks. Spelled as SQL text
#: because the partial index `ix_session_records_expiring` is declared with this
#: same predicate, and only the same spelling lets the planner use it.
_EXPIRING = text("object_key IS NOT NULL AND NOT starts_with(object_key, 'archive/')")


async def expire(
    sessions: SessionFactory,
    storage: StorageBackend,
    *,
    now: datetime | None = None,
) -> int:
    """Expire sealed runs older than `RETENTION`, keeping their inputs; how many.

    A run that cannot be expired (its object unreadable, the bucket away) is
    logged and left for the next pass; the runs behind it are not held up.
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
            .where(_EXPIRING)
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
        run_id, old_key = run.id, run.object_key
        assert old_key is not None
        try:
            await _replace_with_inputs(db, storage, run)
        except Exception:
            logger.exception("could not expire session record run %s", run_id)
            await db.rollback()
            failed.add(run_id)
            return False
    # The old object goes only once the run that named it is gone, so a
    # failure up to here leaves the run whole. One that fails now is left in
    # the bucket, said in the log; the inputs are safe either way.
    try:
        await storage.delete(old_key)
    except Exception:
        logger.exception("expired session record object not deleted: %s", old_key)
    return True


async def _replace_with_inputs(db, storage: StorageBackend, run: SessionRecord) -> None:
    """Write the run's inputs as an archive run, read it back, and replace the
    run with it in one commit. The run's own object is the caller's to delete."""
    assert run.object_key is not None
    body = await storage.download(run.object_key)
    # An object already gone has nothing left to keep: the row goes on its own.
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
    except Exception:
        # Nothing names the archive object yet: it goes with the attempt.
        if archive_key is not None:
            await storage.delete(archive_key)
        raise
    # A commit that fails may still have landed, so the archive object stays
    # whatever happens: at worst it is one more object nothing names.
    await db.commit()


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
    forgotten = 0
    for conversation_id in gone:
        try:
            await _forget(sessions, storage, conversation_id)
        except Exception:
            # The next pass tries this one again; the others go on now.
            logger.exception("could not forget session records of %s", conversation_id)
            continue
        forgotten += 1
    return forgotten


async def _forget(
    sessions: SessionFactory,
    storage: StorageBackend | None,
    conversation_id: uuid.UUID,
) -> None:
    async with sessions() as db:
        # The rows this deletes are exactly the ones whose objects go: a run
        # the sealer commits meanwhile is not among them, and is found on the
        # next pass with its object.
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
        # Objects before the commit: if one cannot be deleted, the rows come
        # back and the next pass tries again (deleting an absent object is
        # not an error).
        if storage is not None:
            for key in filter(None, keys):
                await storage.delete(key)
            # And whatever else is filed under the conversation: an object an
            # expiry could not delete after its commit, or an archive written
            # for a commit that did not land, is named by no row.
            for prefix in (service.PREFIX, ARCHIVE_PREFIX):
                await storage.delete_prefix(f"{prefix}/{conversation_id}/")
        await db.commit()


#: How often the bucket is checked for objects nothing names.
ORPHANS_EVERY_S = 24 * 3600
#: How old an unnamed object is before it goes: past the retention, so the
#: object of any run still kept is named by its row, and an upload the sealer
#: has not yet committed is far younger.
ORPHAN_AFTER = RETENTION + timedelta(days=1)


async def forget_orphans(
    sessions: SessionFactory,
    storage: StorageBackend | None = None,
    *,
    now: datetime | None = None,
) -> int:
    """Delete raw-record objects older than `ORPHAN_AFTER` that no row names;
    how many.

    What leaves one: an expiry whose delete failed after its commit, a commit
    that landed and raised, a sealer that died between its upload and its
    commit. Archives are left alone: the inputs are kept by design, and one a
    failed commit left behind goes with its conversation (`forget_gone`).
    """
    storage = storage or service.bucket()
    if storage is None:
        return 0
    cutoff = (now or datetime.now(UTC)) - ORPHAN_AFTER
    gone = 0
    async for page in storage.listed(f"{service.PREFIX}/"):
        old = [key for key, modified in page if modified < cutoff]
        if not old:
            continue
        async with sessions() as db:
            named = set(
                (
                    await db.execute(
                        select(SessionRecord.object_key).where(
                            SessionRecord.object_key.in_(old)
                        )
                    )
                )
                .scalars()
                .all()
            )
        for key in old:
            if key not in named:
                await storage.delete(key)
                gone += 1
    if gone:
        logger.warning("deleted %d session record objects no row named", gone)
    return gone
