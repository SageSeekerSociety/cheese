"""Ingest the metering proxy's usage log into the platform's books (issue #218).

A subscription turn is metered where its traffic passes: the mitmproxy addon
appends one JSON line per ``/v1/messages`` response, carrying project/topic/
model and the four token buckets. Nothing else has those numbers — interactive
Claude Code reports no usage, and the gateway never sees subscription traffic.
Until this module existed the file just grew (12k rows on dev) while
``resource_usage`` recorded zeros.

Exactly-once: rows land in the SAME transaction that advances the byte-offset
checkpoint (``IngestCheckpoint``), so a crash between batches re-reads at most
what it never committed. The reader consumes only complete lines — a torn tail
line stays for the next pass — and a fingerprint of the file's head detects
rotation: a replaced file is a new generation and restarts from zero.

Credits: a subscription row has no USD price, so it burns the flat token rate
(``tokens_to_credits``) over ALL four buckets — cache reads are not free and
they dominate (one observed task: 2.9M cached vs 141k fresh). This matches the
proxy's own cap arithmetic, so the two brakes count the same thing.

Runs on its own interval task (``SubscriptionUsageIngestRunner``), started from
the app lifespan when ``SUBSCRIPTION_USAGE_LOG`` is set — never hung off the
project scheduler, which ships disabled.
"""

import hashlib
import json
import logging
import uuid
from bisect import bisect_right
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import SessionFactory
from app.domain.block.models import Block
from app.domain.project.repositories import ProjectRepository
from app.domain.room_task.place import room_and_task
from app.domain.usage.credits import tokens_to_credits
from app.domain.usage.models import IngestCheckpoint
from app.domain.usage.repositories import ComputeGrantRepository, UsageRepository
from app.domain.usage.tokens import input_output_tokens

logger = logging.getLogger("cheese.usage.ingest")

SOURCE = "subscription-proxy"
# Enough head bytes to tell one log generation from another (the first line
# carries a timestamp), cheap enough to hash every pass.
_FINGERPRINT_BYTES = 4096
# Rows per transaction: bounds memory and the blast radius of a bad row.
_BATCH_LINES = 2000


def head_fingerprint(path: Path, length: int) -> str:
    """Identity of this file GENERATION: a hash of its first ``length`` bytes.

    ``length`` must lie inside the region already consumed — append-only writes
    never touch it, so the hash is stable across appends and changes only when
    the file is rotated/replaced. Hashing a fixed 4KB head instead was wrong:
    for a file still shorter than that, every append changed the hash and read
    as a rotation, resetting the offset and double-billing the whole file."""
    if length <= 0:
        return ""
    with path.open("rb") as fh:
        return hashlib.sha256(fh.read(length)).hexdigest()


def read_new_lines(
    path: Path, byte_offset: int, max_lines: int = _BATCH_LINES
) -> tuple[list[dict], int]:
    """Complete JSON lines after ``byte_offset``, and the offset consumed to.

    Only lines terminated by a newline are consumed — the proxy appends a full
    line at a time, but a read can still race the write mid-line, and a torn
    line must be left for the next pass rather than half-parsed. Unparseable
    complete lines are consumed and dropped (logged): stopping on them would
    wedge ingestion forever on one bad byte."""
    rows: list[dict] = []
    consumed = byte_offset
    with path.open("rb") as fh:
        fh.seek(byte_offset)
        while len(rows) < max_lines:
            raw = fh.readline()
            if not raw or not raw.endswith(b"\n"):
                break  # EOF or torn tail — next pass
            consumed += len(raw)
            if not raw.strip():
                continue
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning("unparseable usage line at offset %d", consumed)
                continue
            if isinstance(parsed, dict):
                rows.append(parsed)
    return rows, consumed


def _uuid_or_none(value: object) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return None


# How long after its first block an attributed unit may still claim proxy
# traffic. Agent work can run for tens of minutes, so the window is generous but
# bounded: a stray line days later must not attach to the topic's last work id.
_WORK_ATTRIBUTION_WINDOW_S = 6 * 3600


class WorkIndex:
    """Timestamp → the work id active in a topic at that moment.

    The metering proxy logs one line per ``/v1/messages`` response and knows
    nothing about platform attribution; blocks carry the originating message or
    work id. Joining the two makes the aggregate count attributed work instead
    of HTTP calls. Starts are loaded once per topic per ingestion pass.
    """

    def __init__(self) -> None:
        self._starts: dict[uuid.UUID, list[tuple[datetime, uuid.UUID]]] = {}

    async def work_at(
        self, session: AsyncSession, topic_id: uuid.UUID | None, ts: object
    ) -> uuid.UUID | None:
        if topic_id is None:
            return None
        try:
            moment = datetime.fromtimestamp(float(ts), UTC)  # type: ignore[arg-type]
        except (TypeError, ValueError, OSError, OverflowError):
            return None
        starts = self._starts.get(topic_id)
        if starts is None:
            starts = await self._load(session, topic_id)
            self._starts[topic_id] = starts
        idx = bisect_right([s for s, _ in starts], moment) - 1
        if idx < 0:
            return None
        started_at, work_id = starts[idx]
        if (moment - started_at).total_seconds() > _WORK_ATTRIBUTION_WINDOW_S:
            return None
        return work_id

    async def _load(
        self, session: AsyncSession, topic_id: uuid.UUID
    ) -> list[tuple[datetime, uuid.UUID]]:
        # `topic_id` is a place id and may name a thread, whose blocks carry the
        # ROOM's topic_id. Matching on it raw would find nothing and quietly
        # leave every one of that thread's rows unattributed.
        room_id, task_id = await room_and_task(session, topic_id)
        stmt = (
            select(Block.turn_id, func.min(Block.created_at))
            .where(
                Block.topic_id == room_id,
                Block.task_id.is_(None)
                if task_id is None
                else Block.task_id == task_id,
                Block.turn_id.is_not(None),
            )
            .group_by(Block.turn_id)
            .order_by(func.min(Block.created_at))
        )
        rows = (await session.execute(stmt)).all()
        return [(started, work_id) for work_id, started in rows if work_id and started]


async def _land_row(session: AsyncSession, row: dict, work_index: WorkIndex) -> bool:
    """One proxy record → one usage row + credit deduction. False = skipped
    (unattributable or unknown project) — the numbers still exist in the log,
    but nothing here can say whose books they belong in."""
    project_id = _uuid_or_none(row.get("project_id"))
    if project_id is None:
        return False
    if await ProjectRepository(session).get(project_id) is None:
        return False
    topic_id = _uuid_or_none(row.get("topic_id"))
    # Cache reads fold into the input count — AgentUsage has no cache field,
    # and leaving them out would under-report work by more than it reports.
    input_tokens, output_tokens = input_output_tokens(row)
    if input_tokens + output_tokens <= 0:
        return False
    await UsageRepository(session).add(
        project_id=project_id,
        topic_id=topic_id,
        model=str(row.get("model") or ""),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        # A subscription is billed by the month, not by the token: this row has
        # no USD price and never will. 0.0 here means "no price", NOT "free" —
        # the aggregate keeps them apart via `unpriced_tokens` so the panel can
        # say 未知 instead of printing $0.0000 over 2.28M tokens.
        cost_usd=0.0,
        route="subscription",
        # The proxy log has no work id and one attributed unit can make many
        # /v1/messages calls. Use the nearest block-carried id by timestamp.
        turn_id=await work_index.work_at(session, topic_id, row.get("ts")),
    )
    total = int(row.get("total_tokens") or 0) or (input_tokens + output_tokens)
    await ComputeGrantRepository(session).consume(project_id, tokens_to_credits(total))
    return True


async def ingest_once(
    session_factory: SessionFactory, path: Path, source: str = SOURCE
) -> dict[str, int]:
    """One ingestion pass. Returns counters (for logs and tests)."""
    if not path.is_file():
        return {"landed": 0, "skipped": 0}
    size = path.stat().st_size
    landed = skipped = 0
    async with session_factory() as session:
        ckpt = await session.get(IngestCheckpoint, source)
        offset = ckpt.byte_offset if ckpt else 0
        if ckpt and offset > 0:
            probe = min(_FINGERPRINT_BYTES, offset)
            if head_fingerprint(path, probe) != ckpt.fingerprint:
                # New file generation (rotation/replacement) — restart from zero.
                offset = 0
        if offset > size:
            # Same head but shorter than we consumed: truncated in place.
            offset = 0
        rows, new_offset = read_new_lines(path, offset)
        work_index = WorkIndex()
        for row in rows:
            if await _land_row(session, row, work_index):
                landed += 1
            else:
                skipped += 1
        if ckpt is None:
            ckpt = IngestCheckpoint(source=source)
            session.add(ckpt)
        ckpt.byte_offset = new_offset
        ckpt.fingerprint = head_fingerprint(path, min(_FINGERPRINT_BYTES, new_offset))
        await session.commit()
    if landed or skipped:
        logger.info(
            "subscription usage ingested: %d landed, %d skipped, offset %d",
            landed,
            skipped,
            new_offset,
        )
    return {"landed": landed, "skipped": skipped}
