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

Credits: a subscription call is priced like a gateway call. The gateway's
model table (``feature_stats.pricing.model_rates``) carries price-only entries
for the Claude models, and each call's buckets — fresh input, output, cache
reads, cache writes to the five-minute and to the one-hour cache — are billed at
that model's rates; the credits are
that cost over ``CREDIT_USD``, the same quotient as gateway rows.
Cache reads dominate (one observed task: 2.9M cached vs 141k fresh) and are
billed at the cache-read rate, not as fresh input.

A model with no price still lands its usage row, at cost 0 and no credits, and
the pass logs which models those were. The table is read once per pass; when
the gateway is configured but cannot be asked, the pass lands nothing and the
checkpoint stays put, so an outage never makes a stretch of the log free.

``ingest_once`` runs on its own interval (``app/core/background.py``) when
``SUBSCRIPTION_USAGE_LOG`` is set.
"""

import hashlib
import json
import logging
import re
import uuid
from bisect import bisect_right
from collections import Counter
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import SessionFactory
from app.domain.block.models import Block
from app.domain.conversation.models import Conversation
from app.domain.feature_stats import pricing
from app.domain.project.repositories import ProjectRepository
from app.domain.usage.credits import spend_to_credits
from app.domain.usage.ledger import Ledger, Rates, payer_for_project
from app.domain.usage.models import IngestCheckpoint

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


def _count(value: object) -> int:
    """A token count off a JSON line: ``None``, strings and missing keys all
    show up in practice, and none of them may stop the pass."""
    if value is None:
        return 0
    try:
        return max(0, int(value))  # type: ignore[call-overload]
    except (TypeError, ValueError):
        return 0


# A dated snapshot name (``claude-haiku-4-5-20251001``) is priced as its model.
_SNAPSHOT = re.compile(r"-\d{8}$")


def rates_for(model: str, table: Mapping[str, tuple]) -> Rates | None:
    """``model``'s rates in the gateway's table, None when it has none."""
    for name in (model, _SNAPSHOT.sub("", model)):
        rate = table.get(name)
        if rate is not None:
            return Rates(*rate)
    return None


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
        # One conversation's own blocks: a task's sit under its own id, and
        # folding them into the room's would attribute the same turn twice.
        stmt = (
            select(Block.turn_id, func.min(Block.created_at))
            .where(
                Block.conversation_id == topic_id,
                Block.turn_id.is_not(None),
            )
            .group_by(Block.turn_id)
            .order_by(func.min(Block.created_at))
        )
        rows = (await session.execute(stmt)).all()
        return [(started, work_id) for work_id, started in rows if work_id and started]


async def _land_row(
    session: AsyncSession,
    row: dict,
    work_index: WorkIndex,
    table: Mapping[str, tuple],
    unpriced: Counter[str],
) -> bool:
    """One proxy record → one usage row + credit deduction. False = skipped
    (unattributable or unknown project) — the numbers still exist in the log,
    but nothing here can say whose books they belong in."""
    project_id = _uuid_or_none(row.get("project_id"))
    if project_id is None:
        return False
    if await ProjectRepository(session).get(project_id) is None:
        return False
    # The proxy's ``topic_id`` names the conversation the seat runs in: a room,
    # or a task or thread inside one. The usage row's own reference is to the
    # conversation, so that is where it is looked up.
    topic_id = _uuid_or_none(row.get("topic_id"))
    if topic_id is not None:
        conversation = await session.scalar(
            select(Conversation.id)
            .where(Conversation.id == topic_id, Conversation.project_id == project_id)
            .with_for_update(read=True, key_share=True)
        )
        if conversation is None:
            logger.warning(
                "usage conversation %s is absent from project %s; "
                "retaining project usage",
                topic_id,
                project_id,
            )
            topic_id = None
    # The proxy logs Anthropic's wire buckets: ``input_tokens`` is fresh input
    # only, the cache reads and writes are reported beside it.
    fresh = _count(row.get("input_tokens"))
    cache_read = _count(row.get("cache_read_input_tokens"))
    cache_write = _count(row.get("cache_creation_input_tokens"))
    if "cache_creation_1h_input_tokens" in row:
        cache_write_1h = min(cache_write, _count(row["cache_creation_1h_input_tokens"]))
    else:
        # A line that does not split its writes by lifetime (written before the
        # proxy logged the split) is priced as all one-hour writes: Claude Code
        # caches for an hour, and over-charging a guess is safer than
        # under-charging it.
        cache_write_1h = cache_write
    output_tokens = _count(row.get("output_tokens"))
    input_tokens = fresh + cache_read + cache_write
    if input_tokens + output_tokens <= 0:
        return False
    model = str(row.get("model") or "")
    rates = rates_for(model, table)
    cost = 0.0
    if rates is None:
        unpriced[model] += 1
    else:
        cost = rates.cost_usd(
            input_tokens, output_tokens, cache_read, cache_write, cache_write_1h
        )
    await Ledger(session).record(
        await payer_for_project(session, project_id),
        credits=spend_to_credits(cost),
        topic_id=topic_id,
        model=model,
        input_tokens=input_tokens,
        cache_read_tokens=cache_read,
        cache_write_tokens=cache_write,
        cache_write_1h_tokens=cache_write_1h,
        output_tokens=output_tokens,
        cost_usd=cost,
        route="subscription",
        # The proxy log has no work id and one attributed unit can make many
        # /v1/messages calls. Use the nearest block-carried id by timestamp.
        turn_id=await work_index.work_at(session, topic_id, row.get("ts")),
    )
    return True


async def ingest_once(
    session_factory: SessionFactory, path: Path, source: str = SOURCE
) -> dict[str, int]:
    """One ingestion pass. Returns counters (for logs and tests)."""
    if not path.is_file():
        return {"landed": 0, "skipped": 0}
    table = await pricing.model_rates()
    if table is None:
        if settings.llm_gateway_admin_base:
            # Configured but not answering: wait for it rather than land a
            # stretch of the log at no price.
            logger.warning("model rates unavailable; subscription ingest deferred")
            return {"landed": 0, "skipped": 0}
        table = {}
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
        unpriced: Counter[str] = Counter()
        for row in rows:
            if await _land_row(session, row, work_index, table, unpriced):
                landed += 1
            else:
                skipped += 1
        if ckpt is None:
            ckpt = IngestCheckpoint(source=source)
            session.add(ckpt)
        ckpt.byte_offset = new_offset
        ckpt.fingerprint = head_fingerprint(path, min(_FINGERPRINT_BYTES, new_offset))
        await session.commit()
    if unpriced:
        logger.warning(
            "subscription usage recorded without a price, nothing charged: %s",
            ", ".join(f"{m or '(no model)'} x{n}" for m, n in unpriced.most_common()),
        )
    if landed or skipped:
        logger.info(
            "subscription usage ingested: %d landed, %d skipped, offset %d",
            landed,
            skipped,
            new_offset,
        )
    return {"landed": landed, "skipped": skipped}
