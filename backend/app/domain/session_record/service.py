"""Keep a session's raw records, seal them into the private bucket, read them back.

Three operations, all on `SessionRecord`:

- `append` lands a page of a mirror's journal rows, scrubbed, one row each. A
  row seen before (same journal position) lands once, so a page read twice
  after a failure changes nothing.
- `seal` joins rows older than `SEAL_AFTER` into one gzip object per run of a
  turn, so the database holds an index and the last few minutes, not the bulk:
  measured on dev in October 2026, a day of sessions journaled about 0.9 GB.
- `read` hands a conversation's records back in the order they were written,
  from whichever of the two homes each run is in.

An object, once sealed, is never deleted. The database index expires after
thirty days; self-describing object keys keep the archive readable afterwards.

What is kept is the journal row as the runner wrote it, the platform's
`cheese` stamp included. The only change is the scrub (`scrubbed`):
credentials go the way `core.obs.scrub_secrets` takes them out of logs (a
field named like a credential included), and NUL, the one character JSONB
cannot hold, becomes U+FFFD.
"""

from __future__ import annotations

import asyncio
import base64
import functools
import gzip
import io
import json
import re
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import SessionFactory
from app.core.obs import scrub_secrets
from app.core.storage import StorageBackend, private_storage
from app.domain.conversation.models import Conversation
from app.domain.session_record import models as session_record_models
from app.domain.session_record.archive import (
    INDEX_PREFIX,
    describe,
    index_key,
    object_key,
)
from app.domain.session_record.images import keep as keep_images
from app.domain.session_record.models import SessionRecord

#: How old a record is before the sealer moves it out of the row. Long enough
#: that the live end of a turn is read from the database; short enough that
#: the database holds minutes of records, not days.
SEAL_AFTER = timedelta(minutes=5)
#: How often the sealer looks.
SEAL_EVERY_S = 60
#: The most one sealed object holds, before gzip: about what a reader opens in
#: one page (the transcript asks 120, the raw view at most 500). A page is what
#: is fetched, and gzip cannot be read from the middle, so an object much
#: larger than a page means every page pays for a whole turn. A turn larger
#: than this is several objects; one record larger than this is an object of
#: its own.
SEAL_MAX_BYTES = 2 << 20
SEAL_MAX_RECORDS = 512
#: How many runs one pass of the sealer seals before it lets go.
SEAL_RUNS_PER_PASS = 200

#: The object key prefix in the private bucket.
PREFIX = "session-records"


class Stream(Protocol):
    """Whose journal: the conversation, the agent and the harness of one
    session (a session's `SessionRef` answers all three)."""

    @property
    def conversation_id(self) -> uuid.UUID: ...

    @property
    def agent_handle(self) -> str: ...

    @property
    def harness(self) -> str: ...


# --- scrubbing ---------------------------------------------------------------


#: What JSONB cannot hold. Binary tool output carries it; the journal writes a
#: lone surrogate nowhere (sqlite refuses to encode one), so only NUL arrives.
_UNSTORABLE = re.compile("\x00")


def _clean(text: str) -> str:
    return scrub_secrets(_UNSTORABLE.sub("�", text))


def scrubbed(value: Any) -> Any:
    """`value` with every string scrubbed, the shape untouched.

    Walked with a stack of its own rather than by recursion: a record nested
    deeper than Python's recursion limit (an MCP result can be) parses, and
    must not then fail here on every drain.
    """
    root = [value]
    pending: list[tuple[list | dict, Any]] = [(root, 0)]
    while pending:
        holder, at = pending.pop()
        item = holder[at]
        if isinstance(item, str):
            holder[at] = _clean(item)
        elif isinstance(item, list):
            copy = list(item)
            holder[at] = copy
            pending.extend((copy, n) for n in range(len(copy)))
        elif isinstance(item, dict):
            copy = {}
            for key, field in item.items():
                name = _clean(key) if isinstance(key, str) else key
                masked = _masked(key, field)
                if masked is not None:
                    copy[name] = masked
                else:
                    copy[name] = field
                    pending.append((copy, name))
            holder[at] = copy
    return root[0]


def _masked(key: object, value: Any) -> str | None:
    """``***`` for a field `scrub_secrets` would take for a credential by its
    name, or None for any other.

    A credential in a record is often a field of its own (a tool's
    ``{"password": ...}``, an env map's ``{"GITHUB_TOKEN": ...}``); the key
    rule only sees it when the name and the value are in one string. The name
    is what makes it a credential, so the whole value goes, whatever it holds
    (a number, a list): the rule itself stops at the first space, quote or
    comma, and would leave the rest of a passphrase behind. An empty or null
    value has nothing to hide and is kept as it was.
    """
    if not isinstance(key, str) or value is None or value in ("", [], {}):
        return None
    return "***" if _names_a_credential(key) else None


@functools.lru_cache(maxsize=4096)
def _names_a_credential(key: str) -> bool:
    """Whether `scrub_secrets`'s key=value rule takes ``key`` for a credential's
    name, asked with a value no other rule touches. A name of several words
    ("Admin password", "Basic usage") is asked by its last one: whole, the
    auth-scheme rule would read "Basic usage=x" as a credential too."""
    words = key.split()
    if not words:
        return False
    return scrub_secrets(f"{words[-1]}=x") != f"{words[-1]}=x"


def _entries(rows: list[dict]) -> list[dict]:
    """Journal rows as stored entries: {"sequence", "at", "record"}, scrubbed."""
    return [
        {
            "sequence": int(row["sequence"]),
            "at": str(row["at"]),
            "record": scrubbed(row["record"]),
        }
        for row in rows
        if isinstance(row.get("record"), dict)
    ]


def _queued(record: dict) -> bool:
    return record.get("type") == "command_lifecycle" and record.get("state") == "queued"


def _turn(record: dict) -> uuid.UUID | None:
    if _queued(record):
        return None
    work = (record.get("cheese") or {}).get("work_id")
    try:
        return uuid.UUID(str(work)) if work else None
    except ValueError:
        return None


def size(entry: dict) -> int:
    return len(json.dumps(entry, ensure_ascii=False).encode())


# --- writing -----------------------------------------------------------------


async def append(sessions: SessionFactory, stream: Stream, rows: list[dict]) -> int:
    """Keep these journal rows of `stream` (each {"sequence", "at", "record"},
    in journal order); how many were new.

    Nothing is kept for a conversation that is gone: its records would have
    nowhere to be read from, and the retention sweep would delete their rows anyway.
    """
    if any(row.get("images") for row in rows):
        await keep_images(rows, stream.conversation_id, private_storage())
    entries = await asyncio.to_thread(_entries, rows)
    if not entries:
        return 0
    values = []
    for entry in entries:
        at = datetime.fromisoformat(entry["at"])
        values.append(
            {
                "id": uuid.uuid4(),
                "conversation_id": stream.conversation_id,
                "turn_id": _turn(entry["record"]),
                "agent_handle": stream.agent_handle,
                "harness": stream.harness,
                "first_sequence": entry["sequence"],
                "last_sequence": entry["sequence"],
                "first_at": at,
                "last_at": at,
                "count": 1,
                "bytes": size(entry),
                "records": [entry],
            }
        )
    async with sessions() as db:
        if await db.get(Conversation, stream.conversation_id) is None:
            return 0
        values = await _not_sealed(db, stream, values)
        if not values:
            return 0
        result = await db.execute(
            insert(SessionRecord)
            .values(values)
            .on_conflict_do_nothing(constraint="uq_session_records_conversation_id")
            .returning(SessionRecord.id)
        )
        kept = len(result.all())
        await db.commit()
    return kept


async def _not_sealed(
    db: AsyncSession, stream: Stream, values: list[dict]
) -> list[dict]:
    """Leave out the rows a sealed run already holds.

    The unique key catches a row read twice while it is still a row of its own;
    once sealed, it is in the middle of a run, and only the run's span says so.
    """
    low = min(v["first_at"] for v in values)
    high = max(v["first_at"] for v in values)
    spans = (
        await db.execute(
            select(
                SessionRecord.first_at,
                SessionRecord.first_sequence,
                SessionRecord.last_at,
                SessionRecord.last_sequence,
            ).where(
                SessionRecord.conversation_id == stream.conversation_id,
                SessionRecord.agent_handle == stream.agent_handle,
                SessionRecord.harness == stream.harness,
                SessionRecord.object_key.is_not(None),
                SessionRecord.first_at <= high,
                SessionRecord.last_at >= low,
            )
        )
    ).all()
    if not spans:
        return values
    return [
        v
        for v in values
        if not any(
            (s.first_at, s.first_sequence)
            <= (v["first_at"], v["first_sequence"])
            <= (s.last_at, s.last_sequence)
            for s in spans
        )
    ]


async def note(
    db: AsyncSession,
    conversation_id: uuid.UUID,
    *,
    agent_handle: str,
    record: dict,
    turn_id: uuid.UUID | None = None,
    harness: str = "machine",
    at: datetime | None = None,
) -> None:
    """Keep one record the machine itself wrote for 现场 (a sync that failed).

    Not a harness journal row (`append`): the machine speaks in its own voice,
    so it gets a sequence of its own and `harness` keeps it out of every
    agent's run. It is sealed and read back exactly like a harness record.

    The turn is the caller's to name, not this function's to guess: the sync
    runs after the turn ended, and the newest turn then (of the conversation,
    or of this seat) can be a co-seated agent's or the next one. A checkpoint
    names the turn it ended; a sync with none is drawn by 现场 as a platform
    line of its own (`as_block` marks harness "machine" as platform)."""
    when = at or datetime.now(UTC)
    sequence = int(when.timestamp() * 1_000_000)
    entry = {"sequence": sequence, "at": when.isoformat(), "record": scrubbed(record)}
    db.add(
        SessionRecord(
            conversation_id=conversation_id,
            turn_id=turn_id,
            agent_handle=agent_handle,
            harness=harness,
            first_sequence=sequence,
            last_sequence=sequence,
            first_at=when,
            last_at=when,
            count=1,
            bytes=size(entry),
            records=[entry],
        )
    )
    await db.flush()


# --- sealing -----------------------------------------------------------------


def bucket() -> StorageBackend | None:
    """The private bucket, or None where this deployment has none: records then
    stay in their rows."""
    if not (
        settings.s3_endpoint_url
        and settings.s3_access_key
        and settings.s3_secret_key
        and settings.transcript_s3_bucket
    ):
        return None
    return private_storage()


async def seal(
    sessions: SessionFactory,
    storage: StorageBackend | None = None,
    *,
    now: datetime | None = None,
) -> int:
    """Seal runs older than `SEAL_AFTER` into the private bucket; how many runs."""
    storage = storage or bucket()
    if storage is None:
        return 0
    cutoff = (now or datetime.now(UTC)) - SEAL_AFTER
    sealed = 0
    while sealed < SEAL_RUNS_PER_PASS:
        if not await _seal_one(sessions, storage, cutoff):
            break
        sealed += 1
    return sealed


async def _seal_one(
    sessions: SessionFactory, storage: StorageBackend, cutoff: datetime
) -> bool:
    async with sessions() as db:
        head = (
            await db.execute(
                select(SessionRecord)
                .where(SessionRecord.object_key.is_(None))
                .where(SessionRecord.last_at < cutoff)
                .order_by(SessionRecord.last_at)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
        ).scalar_one_or_none()
        if head is None:
            return False
        rows = (
            (
                await db.execute(
                    select(SessionRecord)
                    .where(
                        SessionRecord.conversation_id == head.conversation_id,
                        SessionRecord.agent_handle == head.agent_handle,
                        SessionRecord.harness == head.harness,
                        SessionRecord.turn_id.is_(None)
                        if head.turn_id is None
                        else SessionRecord.turn_id == head.turn_id,
                        SessionRecord.object_key.is_(None),
                        SessionRecord.last_at < cutoff,
                    )
                    .order_by(SessionRecord.first_at, SessionRecord.first_sequence)
                    .limit(SEAL_MAX_RECORDS)
                    .with_for_update(skip_locked=True)
                )
            )
            .scalars()
            .all()
        )
        run: list[SessionRecord] = []
        size = 0
        for row in rows:
            if run and size + row.bytes > SEAL_MAX_BYTES:
                break
            run.append(row)
            size += row.bytes
        if not run:
            return False
        entries = [entry for row in run for entry in row.records or []]
        body = await pack(entries)
        key = object_key(run[0], run[-1].last_at)
        # Verify durable bytes before publishing the index and replacing rows.
        # A retry may publish another run; reads deduplicate native identities.
        await storage.upload(io.BytesIO(body), key, "application/gzip")
        if await storage.download(key) != body:
            # i18n-exempt: the sealer's failure, logged and retried; nobody is shown it
            raise RuntimeError(f"session record archive verification failed: {key}")
        # Publication follows verification. Listing data objects would expose
        # an unverified upload while the database still holds its safe copy.
        await storage.upload(
            io.BytesIO(b""), index_key(key), "application/octet-stream"
        )
        await db.execute(
            delete(SessionRecord).where(SessionRecord.id.in_([r.id for r in run]))
        )
        db.add(
            SessionRecord(
                conversation_id=head.conversation_id,
                turn_id=head.turn_id,
                agent_handle=head.agent_handle,
                harness=head.harness,
                first_sequence=run[0].first_sequence,
                last_sequence=run[-1].last_sequence,
                first_at=run[0].first_at,
                last_at=run[-1].last_at,
                count=len(entries),
                bytes=size,
                records=None,
                object_key=key,
            )
        )
        await db.commit()
    return True


async def pack(entries: list[dict]) -> bytes:
    """Entries as one gzip JSONL object's body, an entry a line."""
    return await asyncio.to_thread(_gzip, entries)


def _gzip(entries: list[dict]) -> bytes:
    lines = "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in entries)
    return gzip.compress(lines.encode(), compresslevel=6)


def unpack(body: bytes) -> list[dict]:
    return [
        json.loads(line) for line in gzip.decompress(body).decode().splitlines() if line
    ]


#: How many sealed bodies one page fetches at once. A page's records span
#: several turns, each turn's objects are fetched with a round trip of their
#: own, and those round trips overlap instead of adding up.
SEALED_FETCH_PARALLEL = 8

#: How many bytes of sealed bodies this process keeps. Sealed objects never
#: change (the table refuses UPDATE; the sealer publishes a key only after it
#: has read the bytes back), so a body read for one page is a body the next
#: read — the poll behind every open 现场, the reader paging up — asks for
#: again. The cap bounds what one busy conversation can hold.
SEALED_CACHE_BYTES = 64 << 20

_sealed_bodies: OrderedDict[str, bytes] = OrderedDict()
_sealed_bytes = 0


def _remember_sealed(key: str, body: bytes) -> None:
    global _sealed_bytes
    if key in _sealed_bodies:
        return
    _sealed_bodies[key] = body
    _sealed_bytes += len(body)
    while _sealed_bytes > SEALED_CACHE_BYTES and _sealed_bodies:
        _, gone = _sealed_bodies.popitem(last=False)
        _sealed_bytes -= len(gone)


async def sealed_body(storage: StorageBackend, key: str) -> bytes:
    """One sealed object's bytes, from this process's cache when it has them."""
    body = _sealed_bodies.get(key)
    if body is not None:
        _sealed_bodies.move_to_end(key)
        return body
    body = await storage.download(key)
    if body is None:
        # i18n-exempt: an invariant broken in storage, a 500 for the logs
        raise RuntimeError(f"sealed session records missing: {key}")
    _remember_sealed(key, body)
    return body


async def _sealed_bodies_within(
    storage: StorageBackend, keys: list[str], budget_s: float
) -> dict[str, bytes]:
    """Bodies for `keys` fetched together, giving up on the ones left at the
    budget. Cached bodies are not a wait and are always returned.

    The caller that passes a budget is the record panel's first page: it would
    rather paint the rows it already has than hold the panel while the bucket
    is slow. A body left out here is not lost — it is exactly the page's
    remainder, and reading on names it again.
    """
    deadline = asyncio.get_running_loop().time() + budget_s
    tasks = {key: asyncio.ensure_future(sealed_body(storage, key)) for key in keys}
    done, pending = await asyncio.wait(
        tasks.values(),
        timeout=max(0.0, deadline - asyncio.get_running_loop().time()),
    )
    for task in pending:
        # Let it finish in the background so its body still reaches the cache —
        # the follow-up read then gets it at once instead of paying the round
        # trip the budget refused to wait for.
        task.add_done_callback(lambda t: t.exception())
    return {key: task.result() for key, task in tasks.items() if task in done}


# --- reading -----------------------------------------------------------------


@dataclass(frozen=True)
class Page:
    items: list[dict]
    next: str | None
    #: A budgeted read left records in the bucket it would have fetched in time.
    #: The items are still in order and still the newest ones; what is missing
    #: is on the far side of the sealed objects, and a budget-free read on the
    #: same cursor completes the page.
    deferred: bool = False


class RecordOut(BaseModel):
    """One record as the read route hands it out."""

    sequence: int
    at: str
    turn_id: str | None
    agent_handle: str
    harness: str
    conversation_id: str | None = None
    cursor: str | None = None
    #: The record as the harness wrote it, scrubbed.
    record: dict[str, Any]


class PageOut(BaseModel):
    items: list[RecordOut]
    #: The cursor to read on from, while there are more.
    next: str | None


#: A record's place in a conversation: when its runner wrote it, then whose
#: journal, then where in it.
Key = tuple[datetime, str, str, int]


def _key(item: dict) -> Key:
    return (item["_at"], item["harness"], item["agent_handle"], item["sequence"])


def encode_cursor(key: Key) -> str:
    raw = json.dumps([key[0].isoformat(), key[1], key[2], key[3]])
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> Key:
    """A cursor back to its key; ValueError when it is not one of ours."""
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        at, harness, agent, sequence = json.loads(base64.urlsafe_b64decode(padded))
        when = datetime.fromisoformat(at)
        if when.tzinfo is None:
            raise ValueError("naive time")  # i18n-exempt: caught below, never shown
        return (when, str(harness), str(agent), int(sequence))
    except Exception as exc:  # any malformed cursor is the same refusal
        # i18n-exempt: the route turns this into its own refusal
        raise ValueError("not a session record cursor") from exc


def _page_is_full(found: list[dict], limit: int, newest: bool, edge: datetime) -> bool:
    """Whether a run starting/ending at `edge` can still put a record in the page.

    `found` holds more than `limit` records already: keep the page's own
    records and say whether this run's edge is past the last of them. The trim
    happens here, so `found` never grows past `limit + 1` while the read walks
    the runs.
    """
    if len(found) <= limit:
        return False
    found.sort(key=_key, reverse=newest)
    del found[limit + 1 :]
    return edge < found[limit]["_at"] if newest else edge > found[limit]["_at"]


async def read(
    db: AsyncSession,
    conversation_id: uuid.UUID,
    *,
    turn_id: uuid.UUID | None = None,
    after: Key | None = None,
    limit: int = 200,
    storage: StorageBackend | None = None,
    before: Key | None = None,
    newest: bool = False,
    author: str | None = None,
    search: str | None = None,
    resident_budget_s: float | None = None,
) -> Page:
    """A conversation's records (or one turn's) after `after`, oldest first.

    Runs are read in the order their first record was written, and a run is
    opened (from its row or its object) only while it may hold one of the next
    `limit` records: once one more than that is in hand and the next run starts
    after the last of them, no later run can come before.
    """
    query = select(SessionRecord).where(
        SessionRecord.conversation_id == conversation_id
    )
    if turn_id is not None:
        query = query.where(SessionRecord.turn_id == turn_id)
    if after is not None:
        query = query.where(SessionRecord.last_at >= after[0])
    if before is not None:
        query = query.where(SessionRecord.first_at <= before[0])
    if author is not None:
        query = query.where(SessionRecord.agent_handle == author)
    order = SessionRecord.last_at.desc() if newest else SessionRecord.first_at
    query = query.order_by(order, SessionRecord.id).execution_options(yield_per=50)
    found: list[dict] = []
    seen: set[Key] = set()
    opened: set[str] = set()

    def collect(entries: list[dict], turn, agent: str, harness: str) -> None:
        for entry in entries:
            # Old runners stamped a queued command with the last execution.
            # Keep that evidence intact, but do not treat it as execution;
            # the row and archive turn indexes are only candidate selectors.
            execution = str(turn) if turn and not _queued(entry["record"]) else None
            if turn_id is not None and execution != str(turn_id):
                continue
            if (
                search is not None
                and search.casefold()
                not in json.dumps(entry["record"], ensure_ascii=False).casefold()
            ):
                continue
            at = datetime.fromisoformat(entry["at"])
            item = {
                "sequence": int(entry["sequence"]),
                "at": at.astimezone(UTC).isoformat(),
                "turn_id": execution,
                "agent_handle": agent,
                "harness": harness,
                "record": entry["record"],
                "_at": at,
            }
            key = _key(item)
            if key in seen or (after is not None and key <= after):
                continue
            if before is not None and key >= before:
                continue
            seen.add(key)
            found.append(item)

    # The database still holds every record younger than thirty days — a sealed
    # run keeps its row until the retention sweep — so its rows are read first
    # and the bucket's index is consulted only when they cannot fill the page.
    # They are read in the same widening batches the index walk uses: a page
    # that spans several turns must fetch those bodies together, and one that
    # already has all it needs must stop opening runs.
    sealed = None
    batch: list = []
    #: A sealed run that was not fetched because the budget ran out. Its rows
    #: may hold the newest records this page wants; the caller must not be told
    #: the page is complete.
    skipped_sealed = False
    # `resident_budget_s`: how long a read may wait on the bucket before it
    # answers with what the rows hold and lets the caller ask again. The record
    # panel's first page passes a small one, because that page is what a reader
    # stares at: a skipped sealed body costs a page that fills a moment later
    # (the panel prefetches silently), while a bucket that is slow, throttled or
    # being retried by the SDK costs the whole panel. The wait is on the bucket
    # only, never on the database, so the rows already in hand always give an
    # answer.
    deadline = (
        None
        if resident_budget_s is None
        else asyncio.get_running_loop().time() + resident_budget_s
    )

    async def flush() -> None:
        nonlocal sealed, skipped_sealed
        if not batch:
            return
        keys: list[str] = [r.object_key for r in batch if r.object_key]
        if keys and sealed is None:
            sealed = storage or private_storage()
        # Fetched bodies by key. A key that is absent was not fetched (the
        # budget ran out), which the `.get` below reads as "no body" either way.
        bodies: dict[str, bytes] = {}
        if keys:
            assert sealed is not None
            if deadline is not None:
                left = deadline - asyncio.get_running_loop().time()
                if left <= 0:
                    # Out of budget: this batch's sealed run is left for the
                    # follow-up read rather than fetched now.
                    batch.clear()
                    return
                bodies = await _sealed_bodies_within(sealed, keys, left)
            else:
                bodies = dict(
                    zip(
                        keys,
                        await asyncio.gather(
                            *(sealed_body(sealed, key) for key in keys)
                        ),
                        strict=True,
                    )
                )
        for row in batch:
            body = bodies.get(row.object_key) if row.object_key is not None else None
            if row.object_key is not None and body is None and deadline is not None:
                # Skipped for budget, not missing: its records are only in the
                # bucket, so there is nothing to collect here and the follow-up
                # read is what fetches them. It may hold records this page is
                # missing, so the page cannot be handed back as complete.
                skipped_sealed = True
                continue
            if row.object_key is not None and body is not None:
                opened.add(row.object_key)
                collect(
                    await asyncio.to_thread(unpack, body),
                    row.turn_id,
                    row.agent_handle,
                    row.harness,
                )
            else:
                collect(
                    await open_run(row, storage),
                    row.turn_id,
                    row.agent_handle,
                    row.harness,
                )
        batch.clear()

    width = 1
    async for row in await db.stream_scalars(query):
        if row.object_key is not None and row.object_key in opened:
            continue
        if _page_is_full(found, limit, newest, row.last_at if newest else row.first_at):
            break
        batch.append(row)
        if len(batch) >= width:
            await flush()
            width = min(width * 2, SEALED_FETCH_PARALLEL)
    await flush()
    # The bucket's index survives the database's thirty-day index: what the
    # rows no longer hold is still there, under its own key. Listing it is a
    # round trip, so it is skipped whenever it cannot name anything this page
    # is missing:
    #
    # - A forward read (`after`) from inside the retention window. Every run it
    #   could name is filtered to `last_at >= after[0]`, and a row is swept only
    #   once its own `last_at` is past the window — so each of those runs still
    #   has its row, and the listing could only hand back what the rows already
    #   gave. This is the read the open panel makes: its cursor is seconds old,
    #   every three seconds, and that listing was the renewal's whole cost —
    #   right up to the rate limit that made the read answer as a timeout.
    # - A newest-page read the rows already overfilled: there is a boundary to
    #   hand back and nothing older could displace it. At exactly `limit` the
    #   reader must still be sent into the archive — the surplus that would
    #   have named their way up may be records whose rows were swept.
    #
    # Nothing else may skip it. A backward read (`before`, or neither cursor)
    # has no floor under it: the record just above the page may be one whose
    # row the sweep has already taken.
    #
    # The window is `models.RETENTION` — the very value `retention.expire`
    # deletes by — read through the module rather than imported as a name: a
    # reader holding its own copy would skip the listing while a sweep by the
    # real value had taken the rows, and answer a short page as if it were
    # complete. (It lives in `models`, below both this module and `retention`,
    # because `retention` imports this one.)
    window = datetime.now(UTC) - session_record_models.RETENTION
    inside_the_window = after is not None and after[0] >= window
    archive = storage or bucket()
    # A budgeted read walks neither the index nor the bodies below: both are
    # bucket round trips, and the caller that passed the budget wanted the rows
    # now. It reads on afterwards, budget-free, for whatever the rows could not
    # give. So this listing is the unbudgeted readers' alone.
    if (
        archive is not None
        and not inside_the_window
        and deadline is None
        and not (newest and len(found) > limit)
    ):
        runs = []
        async for key in archive.list_keys(f"{INDEX_PREFIX}/{conversation_id}/"):
            run = describe(key)
            if turn_id is not None and run["turn_id"] != str(turn_id):
                continue
            if author is not None and run["agent_handle"] != author:
                continue
            if after is not None and run["last_at"] < after[0]:
                continue
            if before is not None and run["first_at"] > before[0]:
                continue
            if run["object_key"] in opened:
                continue
            runs.append(run)
        runs.sort(
            key=lambda r: r["last_at"] if newest else r["first_at"], reverse=newest
        )
        # Fetch the run the page needs, then two, four…: the first fetch pays
        # one round trip, and the ones after it overlap rather than queue. A
        # page that one object already fills never fetches a second.
        at = 0
        width = 1
        while at < len(runs):
            if _page_is_full(
                found,
                limit,
                newest,
                runs[at]["last_at"] if newest else runs[at]["first_at"],
            ):
                break
            batch = runs[at : at + width]
            bodies = await asyncio.gather(
                *(sealed_body(archive, run["object_key"]) for run in batch)
            )
            stopped = False
            for run, body in zip(batch, bodies, strict=True):
                if _page_is_full(
                    found, limit, newest, run["last_at"] if newest else run["first_at"]
                ):
                    stopped = True
                    break
                opened.add(run["object_key"])
                collect(
                    await asyncio.to_thread(unpack, body),
                    run["turn_id"],
                    run["agent_handle"],
                    run["harness"],
                )
                at += 1
            if stopped:
                break
            width = min(width * 2, SEALED_FETCH_PARALLEL)
    found.sort(key=_key, reverse=newest)
    page = found[:limit]
    cursor = encode_cursor(_key(page[-1])) if len(found) > limit else None
    if newest:
        page.reverse()
    for item in page:
        del item["_at"]
    # A budgeted read that the rows did not overfill has not seen the archive —
    # the listing it would have taken to reach it is itself a bucket round trip,
    # which is what the budget exists to avoid. So the page may be missing
    # records and must not be handed back as complete: `deferred` says so, and
    # the caller re-reads (this time without a budget, from the cache the
    # skipped fetches filled). When the rows already overfilled the page there
    # is a boundary and nothing above the page could displace it, so the page is
    # as complete as an unbudgeted read's would be.
    deferred = skipped_sealed or (deadline is not None and len(found) <= limit)
    return Page(page, cursor, deferred=deferred)


async def open_run(row: SessionRecord, storage: StorageBackend | None) -> list[dict]:
    if row.records is not None:
        return row.records
    assert row.object_key is not None
    bucket = storage or private_storage()
    body = await sealed_body(bucket, row.object_key)
    return await asyncio.to_thread(unpack, body)
