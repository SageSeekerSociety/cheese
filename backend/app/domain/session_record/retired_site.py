"""Move the rows the room's old 现场 left in `blocks` into the private bucket.

Release one (#4448) removed the writers of the old 现场: a tool step with its
output, a subagent's conclusion, the session's narration, each a hidden event
block. What they wrote is an agent's trace, and agent traces are kept for good,
so the rows leave the database only by way of the bucket. `RETIRED_SITE_ROWS`
names them by what they carry, exactly as `scripts/dev/count-retired-site.py`
counts them; the hidden notices that are still written (an edited message, a
memory change) carry none of it and stay.

One batch is one conversation's rows, at most `BATCH_ROWS`, in one transaction:

1. the rows, as Postgres renders each one (`row_to_json`), become the lines of
   a gzip JSONL object at `archive/site-event-blocks/<conversation>/<batch>.jsonl.gz`;
2. the object is read back and must equal, byte for byte, what was sent;
3. only then are exactly those rows deleted, and the batch is recorded in
   `site_event_archives`, in the same commit.

Anything that fails rolls the transaction back, so the rows stay where they
were. Nothing deletes an object under `ARCHIVE_PREFIX`: an object a failed
attempt uploaded stays as well, and its rows are archived again by a later
batch under a new name. A deployment without a private bucket keeps every row.

A failure is retried with a growing wait (`RETRY_FIRST_S` doubling up to
`RETRY_MAX_S`). It is reported at ERROR, which alerts, when it starts and when
it changes stage or kind; a retry that fails the same way logs a WARNING.

A deleted row that a kept message replies to leaves that message's `reply_to`
empty (`ON DELETE SET NULL`); on dev in October 2026 that was five messages.

A conversation's newest block stays while it is the newest, retired or not.
A new block takes the number after the largest one in its conversation
(`block_numbered()`), and a read cursor holds the largest number there when
it was set, hidden rows included; deleting the newest block would hand its
number to the next one, which a reader past it would never see as unread.
A later pass takes it once a newer block exists.

Once no row it may delete is left, the job does nothing more in this process.
No writer is left, so only a new block above a kept newest row makes one again.
"""

from __future__ import annotations

import asyncio
import gzip
import io
import logging
import time
import uuid
from collections.abc import Callable

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.types import Uuid

from app.core.db import SessionFactory
from app.core.storage import StorageBackend
from app.domain.session_record import service
from app.domain.session_record.models import SiteEventArchive

logger = logging.getLogger(__name__)

#: The rows release two deletes, as a `WHERE` over `blocks`; the same text as
#: `scripts/dev/count-retired-site.py`, which counts them read-only on dev.
RETIRED_SITE_ROWS = """
    kind = 'event'
    AND meta::jsonb @> '{"in_room": false}'
    AND (
        meta::jsonb ? 'tool'
        OR meta::jsonb ? 'subagent'
        OR meta::jsonb @> '{"progress": true}'
    )
"""

#: What the job may delete: the retired rows, less each conversation's newest
#: block (the module docstring says why).
_TAKEN = f"""
    {RETIRED_SITE_ROWS}
    AND seq < (
        SELECT max(newest.seq) FROM blocks AS newest
        WHERE newest.conversation_id = blocks.conversation_id
    )
"""

ARCHIVE_PREFIX = "archive/site-event-blocks"
#: A batch's rows. Rows averaged 1.4 kB on dev (54 kB the largest), so an object
#: is a megabyte or two before gzip.
BATCH_ROWS = 1000
#: Batches one pass archives before it yields to the next tick.
BATCHES_PER_PASS = 20
EVERY_S = 60
RETRY_FIRST_S = 60
RETRY_MAX_S = 3600

_NEXT_CONVERSATION = text(f"SELECT conversation_id FROM blocks WHERE {_TAKEN} LIMIT 1")
_BATCH = text(
    f"""
    SELECT id, row_to_json(blocks)::text AS line FROM blocks
    WHERE conversation_id = :conversation AND {_TAKEN}
    ORDER BY seq, id
    LIMIT :limit
    FOR UPDATE
    """
)
_DELETE = text(
    f"DELETE FROM blocks WHERE id = ANY(:ids) AND {_TAKEN} RETURNING id"
).bindparams(bindparam("ids", type_=ARRAY(Uuid)))


class BatchFailed(Exception):
    """A batch that did not leave the database, and the step it stopped at."""

    def __init__(self, stage: str, key: str | None) -> None:
        super().__init__(f"{stage} {key or ''}".strip())
        self.stage = stage


def object_key(conversation: uuid.UUID, batch: uuid.UUID) -> str:
    return f"{ARCHIVE_PREFIX}/{conversation}/{batch.hex}.jsonl.gz"


async def archive_batch(sessions: SessionFactory, storage: StorageBackend) -> int:
    """Archive and delete one batch; how many rows it took, 0 when none is left."""
    stage, key = "select", None
    try:
        async with sessions() as db:
            rows = []
            while not rows:
                conversation = (await db.execute(_NEXT_CONVERSATION)).scalar()
                if conversation is None:
                    return 0
                # Empty when the conversation's rows went while this waited for
                # their lock (the conversation was deleted): pick the next one.
                rows = (
                    await db.execute(
                        _BATCH, {"conversation": conversation, "limit": BATCH_ROWS}
                    )
                ).all()
            batch = uuid.uuid4()
            key = object_key(conversation, batch)
            body = await asyncio.to_thread(
                gzip.compress,
                "".join(row.line + "\n" for row in rows).encode(),
                6,
            )
            stage = "upload"
            await storage.upload(io.BytesIO(body), key, "application/gzip")
            stage = "verify"
            if await storage.download(key) != body:
                # i18n-exempt: the archiver's failure, logged and retried; nobody is shown it
                raise RuntimeError("the bucket's copy differs from what was sent")
            stage = "delete"
            ids = [row.id for row in rows]
            deleted = set((await db.execute(_DELETE, {"ids": ids})).scalars())
            if deleted != set(ids):
                # i18n-exempt: the archiver's failure, logged and retried; nobody is shown it
                raise RuntimeError(
                    f"deleted {len(deleted)} rows, not the {len(ids)} archived"
                )
            db.add(
                SiteEventArchive(
                    id=batch,
                    conversation_id=conversation,
                    object_key=key,
                    rows=len(ids),
                    bytes=len(body),
                )
            )
            await db.commit()
            return len(ids)
    except Exception as exc:
        raise BatchFailed(stage, key) from exc


class RetiredSiteArchive:
    """The periodic job: its retry clock, the failure it last reported, and
    whether anything is left, kept for the life of the process."""

    def __init__(
        self,
        sessions: SessionFactory,
        bucket: Callable[[], StorageBackend | None] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._sessions = sessions
        self._bucket = bucket or service.bucket
        self._clock = clock
        self._done = False
        self._failing: str | None = None
        self._attempts = 0
        self._retry_at = 0.0

    async def run(self) -> dict[str, int]:
        if self._done or self._clock() < self._retry_at:
            return {}
        storage = self._bucket()
        if storage is None:
            return {}
        batches = rows = 0
        try:
            for _ in range(BATCHES_PER_PASS):
                taken = await archive_batch(self._sessions, storage)
                if not taken:
                    self._done = True
                    logger.info("no retired 现场 rows are left in blocks")
                    break
                batches += 1
                rows += taken
        except BatchFailed as failure:
            self._failed(failure)
        else:
            if self._failing is not None:
                logger.info("retired 现场 rows archive again after: %s", self._failing)
            self._failing, self._attempts, self._retry_at = None, 0, 0.0
        return {"batches": batches, "rows": rows}

    def _failed(self, failure: BatchFailed) -> None:
        cause = failure.__cause__
        state = f"{failure.stage}: {type(cause).__name__}"
        wait = min(RETRY_FIRST_S * 2**self._attempts, RETRY_MAX_S)
        self._attempts += 1
        self._retry_at = self._clock() + wait
        if state != self._failing:
            logger.error(
                "retired 现场 rows not archived at %s; they stay, retry in %ss",
                failure,
                wait,
                exc_info=cause,
            )
        else:
            logger.warning(
                "retired 现场 rows still not archived at %s (%s); retry in %ss",
                failure,
                cause,
                wait,
            )
        self._failing = state
