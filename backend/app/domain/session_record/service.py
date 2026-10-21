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
#: The most one sealed object holds, before gzip. A turn larger than this is
#: several objects; one record larger than this is an object of its own.
SEAL_MAX_BYTES = 8 << 20
SEAL_MAX_RECORDS = 2000
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


def _turn(record: dict) -> uuid.UUID | None:
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

    The sync runs at a turn's end, after the harness's own last record, and the
    machine is given no turn id. With none, the note joins the last turn this
    seat's records carry — the one whose checkpoint it is, not a co-seated
    agent's newer turn."""
    when = at or datetime.now(UTC)
    if turn_id is None:
        turn_id = await _latest_turn(db, conversation_id, agent_handle)
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


async def _latest_turn(
    db: AsyncSession, conversation_id: uuid.UUID, agent_handle: str
) -> uuid.UUID | None:
    """The turn this seat's newest record belongs to, if any.

    Scoped to the seat that is writing the note: a room can seat several
    agents, and another agent's newer turn is not the turn this machine's
    checkpoint ended."""
    return await db.scalar(
        select(SessionRecord.turn_id)
        .where(
            SessionRecord.conversation_id == conversation_id,
            SessionRecord.agent_handle == agent_handle,
            SessionRecord.turn_id.is_not(None),
        )
        .order_by(SessionRecord.last_at.desc(), SessionRecord.id.desc())
        .limit(1)
    )


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


# --- reading -----------------------------------------------------------------


@dataclass(frozen=True)
class Page:
    items: list[dict]
    next: str | None


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
                "turn_id": str(turn) if turn else None,
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

    # The bucket's index survives the database's thirty-day index. Identity and
    # time bounds in the key let us skip bodies belonging to other turns and
    # stop opening runs once the page is full. Objects stay private.
    archive = storage or bucket()
    if archive is not None:
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
            runs.append(run)
        runs.sort(
            key=lambda r: r["last_at"] if newest else r["first_at"], reverse=newest
        )
        for run in runs:
            if len(found) > limit:
                found.sort(key=_key, reverse=newest)
                del found[limit + 1 :]
                edge = run["last_at"] if newest else run["first_at"]
                if edge < found[limit]["_at"] if newest else edge > found[limit]["_at"]:
                    break
            body = await archive.download(run["object_key"])
            opened.add(run["object_key"])
            if body is None:
                # i18n-exempt: an invariant broken in storage, a 500 for the logs
                raise RuntimeError(
                    f"sealed session records missing: {run['object_key']}"
                )
            collect(
                await asyncio.to_thread(unpack, body),
                run["turn_id"],
                run["agent_handle"],
                run["harness"],
            )
    async for row in await db.stream_scalars(query):
        if row.object_key in opened:
            continue
        if len(found) > limit:
            found.sort(key=_key, reverse=newest)
            del found[limit + 1 :]
            edge = row.last_at if newest else row.first_at
            if edge < found[limit]["_at"] if newest else edge > found[limit]["_at"]:
                break
        collect(
            await open_run(row, storage), row.turn_id, row.agent_handle, row.harness
        )
    found.sort(key=_key, reverse=newest)
    page = found[:limit]
    cursor = encode_cursor(_key(page[-1])) if len(found) > limit else None
    if newest:
        page.reverse()
    for item in page:
        del item["_at"]
    return Page(page, cursor)


async def open_run(row: SessionRecord, storage: StorageBackend | None) -> list[dict]:
    if row.records is not None:
        return row.records
    assert row.object_key is not None
    bucket = storage or private_storage()
    body = await bucket.download(row.object_key)
    if body is None:
        # i18n-exempt: an invariant broken in storage, a 500 for the logs
        raise RuntimeError(f"sealed session records missing: {row.object_key}")
    return await asyncio.to_thread(unpack, body)
