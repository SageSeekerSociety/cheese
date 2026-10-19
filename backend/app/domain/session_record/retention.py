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
conversations that no longer exist and deletes their objects and then them.

Records still in their rows (no private bucket configured, so never sealed)
are not expired: the archive needs the bucket.
"""

from __future__ import annotations

import io
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, distinct, exists, not_, select

from app.core.db import SessionFactory
from app.core.storage import StorageBackend
from app.domain.conversation.models import Conversation
from app.domain.session_record import service
from app.domain.session_record.models import SessionRecord

#: How long a session's records are kept whole.
RETENTION = timedelta(days=30)
#: How often the retention sweep looks.
EVERY_S = 600
#: Where the inputs of expired runs are kept, per conversation.
ARCHIVE_PREFIX = "archive/user-messages"
#: Runs expired, and gone conversations forgotten, per pass.
PER_PASS = 200


def is_input(record: dict) -> bool:
    """Whether this record is an input the session was sent.

    Claude Code echoes every input it reads (`--replay-user-messages`):
    the replay is the input, while the other user records carry tool results.
    Codex reports each input as a `userMessage` item, and pi as a user message
    entry; a pi entry marked as a subagent's is the agent's own prompt to it.
    """
    if record.get("type") == "user":
        return record.get("isReplay") is True
    if record.get("method") == "item/completed":
        item = (record.get("params") or {}).get("item") or {}
        return item.get("type") == "userMessage"
    message = record.get("message")
    return (
        record.get("type") == "message"
        and isinstance(message, dict)
        and message.get("role") == "user"
        and not record.get("subagent")
    )


async def sweep(sessions: SessionFactory) -> dict[str, int]:
    """The periodic job: expire old runs, forget gone conversations."""
    storage = service.bucket()
    if storage is None:
        return {"expired": 0, "forgotten": 0}
    return {
        "expired": await expire(sessions, storage),
        "forgotten": await forget_gone(sessions, storage),
    }


async def expire(
    sessions: SessionFactory,
    storage: StorageBackend,
    *,
    now: datetime | None = None,
) -> int:
    """Expire sealed runs older than `RETENTION`, keeping their inputs; how many."""
    cutoff = (now or datetime.now(UTC)) - RETENTION
    expired = 0
    while expired < PER_PASS and await _expire_one(sessions, storage, cutoff):
        expired += 1
    return expired


async def _expire_one(
    sessions: SessionFactory, storage: StorageBackend, cutoff: datetime
) -> bool:
    async with sessions() as db:
        run = (
            await db.execute(
                select(SessionRecord)
                .where(SessionRecord.object_key.is_not(None))
                .where(SessionRecord.object_key.not_like(f"{ARCHIVE_PREFIX}/%"))
                .where(SessionRecord.last_at < cutoff)
                .order_by(SessionRecord.last_at)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
        ).scalar_one_or_none()
        if run is None:
            return False
        assert run.object_key is not None
        old_key = run.object_key
        entries = [
            e for e in await service.open_run(run, storage) if is_input(e["record"])
        ]
        archive: SessionRecord | None = None
        if entries:
            body = await service.pack(entries)
            key = f"{ARCHIVE_PREFIX}/{run.conversation_id}/{uuid.uuid4().hex}.jsonl.gz"
            await storage.upload(io.BytesIO(body), key, "application/gzip")
            # Read back before anything is deleted: the inputs are the one
            # thing kept past the retention, so they must be there first.
            if await storage.download(key) != body:
                # i18n-exempt: a storage fault for the logs; the run is kept
                raise RuntimeError(f"archived inputs did not read back: {key}")
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
                object_key=key,
            )
        await db.execute(delete(SessionRecord).where(SessionRecord.id == run.id))
        if archive is not None:
            db.add(archive)
        await db.commit()
    # After the commit: a run whose object went first would read as missing.
    await storage.delete(old_key)
    return True


async def forget_gone(sessions: SessionFactory, storage: StorageBackend) -> int:
    """Delete the records, objects and archive of conversations that are gone;
    how many conversations."""
    async with sessions() as db:
        gone = (
            (
                await db.execute(
                    select(distinct(SessionRecord.conversation_id))
                    .where(
                        not_(
                            exists().where(
                                Conversation.id == SessionRecord.conversation_id
                            )
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
            keys = (
                (
                    await db.execute(
                        select(SessionRecord.object_key).where(
                            SessionRecord.conversation_id == conversation_id,
                            SessionRecord.object_key.is_not(None),
                        )
                    )
                )
                .scalars()
                .all()
            )
            # Objects first: a row left behind is found again next pass, an
            # object left behind is not.
            for key in filter(None, keys):
                await storage.delete(key)
            await db.execute(
                delete(SessionRecord).where(
                    SessionRecord.conversation_id == conversation_id
                )
            )
            await db.commit()
    return len(gone)
