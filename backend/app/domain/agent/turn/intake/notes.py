"""Native retry, compaction and reachability notice intake."""

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.sentences import say
from app.domain.agent.platform_notices import (
    EVENT_API_RETRY,
    EVENT_CONTEXT_COMPACT,
    EVENT_DEVICE_WAITING,
    SEVERITY_WARN,
    WHO_PLATFORM,
    notice,
)
from app.domain.agent.prompt import _compaction_notice
from app.domain.agent.run_records import record_now, restate_now
from app.domain.agent.service import (
    AgentCompacting,
    AgentRetrying,
)
from app.domain.agent.turn.state.live import LiveWork
from app.domain.room_task.place import PlaceResolver
from app.domain.run_record import service as run_records

logger = logging.getLogger(__name__)


async def _note_retry(
    sessions: async_sessionmaker,
    live: LiveWork,
    topic_id: uuid.UUID,
    turn_id: uuid.UUID,
    event: AgentRetrying,
    *,
    author: str | None,
    inner_id: uuid.UUID | None,
    channel: str,
) -> None:
    """Say the turn is retrying a failed request, on one line per streak.

    The first retry of a streak lands a notice; every later one restates
    that same line with the new count, so ten retries read as one line that
    says 10 rather than ten lines. Not session output (`note_session_output`
    is not told): nothing the session produced crossed here."""
    count = (
        f"{event.attempt}/{event.max_attempts}"
        if event.attempt and event.max_attempts
        else str(event.attempt or "")
    )
    content = say("apiRetryAttempt", attempt=count) if count else say("apiRetry")
    # Claude Code files a failure it cannot classify as `unknown`, and with no
    # HTTP status that means the request got no response at all. Shown as-is,
    # "unknown" tells a reader nothing; say what it means instead.
    if event.status is None and event.error in ("", "unknown"):
        detail = (
            say("apiRetryNoResponseWaited", seconds=round(event.no_response_ms / 1000))
            if event.no_response_ms
            else say("apiRetryNoResponse")
        )
        label = say("labelDetails")
    else:
        said = " ".join(
            part
            for part in (
                event.error,
                f"HTTP {event.status}" if event.status is not None else "",
            )
            if part
        )
        detail = said or None
        label = say("labelServiceWords") if said else None
    meta = {
        **notice(
            EVENT_API_RETRY,
            severity=SEVERITY_WARN,
            who=WHO_PLATFORM,
            detail=detail,
            detail_label=label,
        ),
        "attempt": event.attempt,
        "max_attempts": event.max_attempts,
        "delay_ms": event.delay_ms,
        "at": datetime.now(UTC).isoformat(),
    }
    await _keep_note(
        sessions,
        live.retry_notes,
        topic_id,
        turn_id,
        content,
        meta,
        author=author,
        inner_id=inner_id,
        channel=channel,
    )


async def _note_compaction(
    sessions: async_sessionmaker,
    live: LiveWork,
    turn_id: uuid.UUID,
    event: AgentCompacting,
    *,
    channel: str,
) -> None:
    """Restate the turn's compaction line as over, if it has one.

    Which line that is comes from the room, not from this process's memory:
    dev replaces its backend on every merge, and a compaction that started
    under one backend ends under the next, which never saw the line land."""
    remembered = live.compact_notes.pop(turn_id, None)
    lines = await _running_compactions(sessions, turn_id)
    if remembered is not None and remembered not in lines:
        lines.append(remembered)
    content, meta = _compaction_notice(event)
    for block_id in lines:
        await _restate_note(sessions, block_id, content, meta, channel)


async def _running_compactions(
    sessions: async_sessionmaker, turn_id: uuid.UUID
) -> list[uuid.UUID]:
    """The turn's compaction records that still say it is compacting."""
    try:
        async with sessions() as session:
            return await run_records.running(session, turn_id, EVENT_CONTEXT_COMPACT)
    except Exception:  # noqa: BLE001 — a status line is not worth a turn
        logger.exception("could not read compaction records of turn %s", turn_id)
        return []


async def _note_reachability(
    sessions: async_sessionmaker,
    live: LiveWork,
    project_id: uuid.UUID,
    topic_id: uuid.UUID,
    work_id: uuid.UUID,
    reachable: bool,
    reason: str,
) -> None:
    """Say the turn is waiting for its machine, and later that the wait is
    over — the same line both times."""
    del project_id
    if reachable:
        block_id = live.waiting_notes.pop(work_id, None)
        if block_id is None:
            return
        await _restate_note(
            sessions,
            block_id,
            say("deviceReconnected"),
            {"state": "over", "at": datetime.now(UTC).isoformat()},
            str(topic_id),
        )
        return
    state = live.hook_work.get((topic_id, work_id))
    meta = {
        **notice(
            EVENT_DEVICE_WAITING,
            severity=SEVERITY_WARN,
            who=WHO_PLATFORM,
            detail=reason or None,
            detail_label=say("labelReason") if reason else None,
        ),
        "state": "waiting",
        "at": datetime.now(UTC).isoformat(),
    }
    # The line lands in the conversation the turn runs in: a task's or a
    # 支线's own, under its room.
    async with sessions() as session:
        place = await PlaceResolver(session).conversation(topic_id)
    await _keep_note(
        sessions,
        live.waiting_notes,
        place.room_id if place is not None else topic_id,
        work_id,
        say("deviceWaiting"),
        meta,
        author=state.acting_agent if state is not None else None,
        inner_id=place.inner_id if place is not None else None,
        channel=str(topic_id),
    )


async def _keep_note(
    sessions: async_sessionmaker,
    notes: dict[uuid.UUID, uuid.UUID],
    topic_id: uuid.UUID,
    turn_id: uuid.UUID,
    content: str,
    meta: dict,
    *,
    author: str | None,
    inner_id: uuid.UUID | None,
    channel: str,
) -> None:
    """Keep the turn's record of this kind, or restate the one it has. A run
    record, not a line in the conversation: the 现场 and the status above the
    composer read it."""
    record_id = notes.get(turn_id)
    if record_id is not None:
        await _restate_note(sessions, record_id, content, meta, channel)
        return
    payload = await record_now(
        sessions,
        conversation_id=inner_id or topic_id,
        content=content,
        meta=meta,
        turn_id=turn_id,
        # The agent whose turn this is: the record is about its work, and the
        # 现场 files it under whoever did the work.
        seat=author,
        channel=channel,
    )
    if payload is not None:
        notes[turn_id] = uuid.UUID(payload["id"])


async def _restate_note(
    sessions: async_sessionmaker,
    record_id: uuid.UUID,
    content: str,
    meta: dict,
    channel: str,
) -> None:
    await restate_now(
        sessions, record_id, content=content, meta=meta, channel=channel or None
    )
