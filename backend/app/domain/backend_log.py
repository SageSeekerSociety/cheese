"""Backend error intake → run records the admin page reads (the mirror of
``frontend_log``).

``core.obs`` renders structlog to stdout and stops there — in production that
ends up inside a container, in dev inside a file on the host — so an unhandled
backend exception must also land somewhere the platform can *query*: a run
record (``kind = "backend_error"``). It belongs to no conversation: a room is
read by the people in it, and the platform's own tracebacks are not theirs to
read. The project and conversation the request named are kept in ``meta``, so
the admin page can say where it happened.

**Push, not pull.** Errors are POSTed in (by the process that raised them, or by
this app's own middleware), exactly like the frontend reporter. The alternative
— giving 芝士 a general "read the logs" capability — is not a style choice we
declined: a user project's 分身 would reach the platform's own logs through that
same opening. A push channel exposes no such handle. See #188 for where this
channel's remit stops: it receives reports, nothing else.

**The whole difficulty is granularity, not plumbing.** The defaults below are
tuned for PRODUCTION volume (one broken route can raise thousands of times a
minute), not for the once-per-bug rhythm of development:

- Only *unhandled* failures arrive here. An expected 4xx is normal flow.
- A fingerprint is reported **once per window**; repeats are counted in silence.
- A burst collapses into ONE summary record ("这个错误 5 分钟内 800 次") when its
  window closes — never one record per occurrence.
- A per-project **hourly hard cap** silently drops everything beyond it.

Deliberate under-reporting: anything past the hourly cap is gone, and a
fingerprint-storm large enough to hit `_WINDOW_PRUNE_AT` forfeits the summaries
it drops. That is the intended trade — 宁可漏报也不能刷屏.

What is NOT traded away is the COUNT. A burst used to get its summary only from
the next occurrence of the same fingerprint, so the commonest case — a bug you
fixed, which by definition stops recurring — kept the first detail line and
silently lost "it happened 500 times", usually the number that says how bad it
was. `flush_expired` is on the platform's periodic-job list, so a window closes
on time instead of on the next failure. Late, never absent.

In-memory state matches the platform's single-process reality (same assumption
as the per-topic chat locks and ``frontend_log``'s intake).
"""

import hashlib
import logging
import re
import time
import traceback
import uuid
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import alerting
from app.core.background import PeriodicJobs
from app.core.config import settings
from app.core.db import async_session_factory
from app.core.obs import scrub_secrets
from app.core.sentences import say
from app.domain.conversation.models import Conversation
from app.domain.run_record.service import keep as keep_record

logger = logging.getLogger(__name__)

# 5 minutes: long enough that a flooding error reports at most ~12 times an hour,
# short enough that a fix's effect shows up in the room while you are still
# looking at it.
DEDUP_WINDOW_S = 300.0
# Per project, across ALL fingerprints. 20/hour is roughly "one line every three
# minutes at worst" — noticeable in a conversation, not a wall.
MAX_BLOCKS_PER_HOUR = 20
RATE_WINDOW_S = 3600.0
# Bound the bookkeeping under a fingerprint-storm (each distinct error is a key).
_WINDOW_PRUNE_AT = 1024

_CONTENT_MESSAGE_LIMIT = 200


class BackendErrorIn(BaseModel):
    """One unhandled backend failure, as reported by whoever caught it."""

    message: str = Field(min_length=1, max_length=1000)
    # Exception class name — the most stable half of an error's identity.
    exc_type: str | None = Field(default=None, max_length=200)
    stack: str | None = Field(default=None, max_length=8000)
    # Where it blew up: "POST /api/topics/{id}/chat" for a request, or
    # "module:line" for anything without one.
    where: str | None = Field(default=None, max_length=300)
    # Correlation id (obs.py binds one per request) so a report can be joined
    # back to the stdout stream when someone *does* have the logs.
    request_id: str | None = Field(default=None, max_length=64)
    # How a model turn ended, as its proxy observed it. Separate fields rather
    # than a sentence inside `message` on purpose: `fingerprint()` keys on the
    # message, so a number in it would turn one recurring failure into a new
    # error every time the turn was cut somewhere else.
    #
    # `received_events` — SSE events already forwarded to the caller. None when
    # there was no stream to count; 0 is the silent stall, where the model said
    # nothing at all before the stream died.
    received_events: int | None = Field(default=None, ge=0)
    # Class of upstream failure the proxy recognised: connection, timeout,
    # transport, dns, tls, rate_limit, http_error, http_auth, egress_offline.
    upstream_failure: str | None = Field(default=None, max_length=40)
    # Who ended the turn first: client, upstream, gateway, or the proxy itself.
    ended_by: str | None = Field(default=None, max_length=20)


class BackendErrorBatchIn(BaseModel):
    # Both optional: a scoped agent credential already names the room, and the
    # route prefers the token's claims over anything the body asserts.
    project_id: uuid.UUID | None = None
    topic_id: uuid.UUID | None = None
    errors: list[BackendErrorIn] = Field(min_length=1, max_length=10)


# Volatile substrings that would otherwise make every occurrence of the SAME bug
# look unique: the uuid in a path, a row id, a port, a byte offset. Without this
# a broken /api/topics/{id}/... route defeats dedup completely — every caller
# brings their own uuid, so every occurrence is a "new" error.
_VOLATILE_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|\d{3,}",
    re.IGNORECASE,
)
_FRAME_RE = re.compile(r'^\s*File "(?P<file>[^"]+)", line (?P<line>\d+)', re.MULTILINE)


def _normalize(text: str) -> str:
    return _VOLATILE_RE.sub("*", text)


def _throw_site(stack: str | None) -> str:
    """The DEEPEST traceback frame — the actual throw site. The shallow frames
    are middleware and are identical for every error in the process, so keying on
    them would merge unrelated bugs."""
    if not stack:
        return ""
    frames = _FRAME_RE.findall(stack)
    if not frames:
        return ""
    file, line = frames[-1]
    return f"{file}:{line}"


def fingerprint(err: BackendErrorIn) -> str:
    """Identity of an error for dedup: exception type + normalized message +
    normalized location + throw site. Ids and numbers are normalized away (see
    ``_VOLATILE_RE``); the full stack is excluded because async frames vary run
    to run for one bug."""
    raw = "\n".join(
        (
            err.exc_type or "",
            _normalize(err.message),
            _normalize(err.where or ""),
            _throw_site(err.stack),
        )
    )
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


@dataclass
class _Window:
    opened_at: float
    count: int = 1
    # Enough to render AND route this window's summary line without a request to
    # read it off. The flusher below closes windows long after the failing call
    # is gone, so whatever it needs has to be captured here at open time.
    sample: "BackendErrorIn | None" = None
    topic_id: uuid.UUID | None = None
    project_uuid: uuid.UUID | None = None


@dataclass(frozen=True)
class ExpiredBurst:
    """A window that closed while nobody was looking — its summary is owed."""

    sample: BackendErrorIn
    count: int
    project_uuid: uuid.UUID | None
    topic_id: uuid.UUID | None


@dataclass(frozen=True)
class Verdict:
    """What the intake decided about one occurrence.

    ``detail`` — first sighting in a window, report it in full.
    ``summary`` — a window just closed holding ``count`` occurrences; report ONE
    line standing in for all of them.
    ``drop`` — say nothing (duplicate inside a window, or over the hourly cap).
    """

    kind: Literal["detail", "summary", "drop"]
    count: int = 1

    def __bool__(self) -> bool:
        return self.kind != "drop"


DROP = Verdict("drop", 0)


@dataclass
class BackendErrorIntake:
    """Dedup + burst-summarization + hourly cap. Injectable clock for tests."""

    _windows: dict[tuple[str, str], _Window] = field(default_factory=dict)
    _hourly: dict[str, list[float]] = field(default_factory=dict)

    def admit(
        self,
        project_id: str,
        fp: str,
        now: float | None = None,
        *,
        sample: BackendErrorIn | None = None,
        topic_id: uuid.UUID | None = None,
        project_uuid: uuid.UUID | None = None,
    ) -> Verdict:
        now = time.time() if now is None else now
        key = (project_id, fp)
        window = self._windows.get(key)

        if window is not None and now - window.opened_at < DEDUP_WINDOW_S:
            # Inside the window: silent, but counted — the tally is what the
            # eventual summary line reports.
            window.count += 1
            return DROP

        # The window is absent or has just expired. Whatever it accumulated is
        # what a summary would announce; a lone occurrence needs no summary, it
        # was already reported in full.
        pending = window.count if window is not None else 0
        self._prune(now)
        self._windows[key] = _Window(
            now, sample=sample, topic_id=topic_id, project_uuid=project_uuid
        )

        if not self._take_slot(project_id, now):
            return DROP
        if pending > 1:
            return Verdict("summary", pending)
        return Verdict("detail", 1)

    def sweep(
        self, now: float | None = None, *, force: bool = False
    ) -> list[ExpiredBurst]:
        """Close every expired window, whether or not its error ever came back.

        Without this, a burst's summary was emitted only by the NEXT occurrence
        of the same fingerprint — and the most common shape of a bug you just
        fixed is that there is no next occurrence. The room would keep the first
        detail line and silently lose the fact that it happened 500 times, which
        is usually the number that tells you how bad it was.

        Reporting late is fine here; reporting never is not. ``force`` closes
        every window this intake still holds, however young — for the caller
        that is itself about to stop existing, and whose open windows would
        otherwise go with it (``flush_all``).
        """
        now = time.time() if now is None else now
        bursts: list[ExpiredBurst] = []
        for key, window in list(self._windows.items()):
            if not force and now - window.opened_at < DEDUP_WINDOW_S:
                continue
            del self._windows[key]
            if window.count <= 1:
                # It was reported in full when it opened; "1 次" adds nothing.
                continue
            if window.sample is None:
                continue
            if not self._take_slot(key[0], now):
                continue  # over the hourly cap — the cap outranks the summary
            bursts.append(
                ExpiredBurst(
                    window.sample, window.count, window.project_uuid, window.topic_id
                )
            )
        return bursts

    def _take_slot(self, project_id: str, now: float) -> bool:
        """Consume one of the project's hourly block budget. False = over cap."""
        cutoff = now - RATE_WINDOW_S
        stamps = [t for t in self._hourly.get(project_id, []) if t > cutoff]
        over = len(stamps) >= MAX_BLOCKS_PER_HOUR
        if not over:
            stamps.append(now)
        self._hourly[project_id] = stamps
        return not over

    def _prune(self, now: float) -> None:
        if len(self._windows) < _WINDOW_PRUNE_AT:
            return
        cutoff = now - DEDUP_WINDOW_S
        # Dropping an expired window forfeits its summary line. That only happens
        # under a fingerprint-storm, where staying bounded matters more.
        self._windows = {k: w for k, w in self._windows.items() if w.opened_at > cutoff}


intake = BackendErrorIntake()


def _stream_note(err: BackendErrorIn) -> str:
    """What the metering proxy watched a dropped model turn do, in one bracket.

    Display only. `fingerprint()` reads `message`, so nothing here can split one
    recurring failure into several records."""
    parts = []
    if err.upstream_failure:
        parts.append(f"upstream: {err.upstream_failure}")
    if err.received_events is not None:
        parts.append(f"events: {err.received_events}")
    if err.ended_by:
        parts.append(f"ended by: {err.ended_by}")
    return f" [{', '.join(parts)}]" if parts else ""


#: What the metering proxy calls a model turn that failed or never happened.
#: Keyed on exc_type and not on `where`: the two lanes report from different
#: places (`model gateway`, `model subscription`) and both have to alert.
#:
#: ClientAbandonedTurn is deliberately NOT here. A caller that hung up is
#: recorded, because that is a shape which used to leave no trace at all, but by
#: itself it is not evidence that the model failed the room — and an hour of
#: people pressing Esc would take the alert slot from the failures underneath.
_MODEL_FAILURE_TYPES = frozenset(
    {
        "GatewayHTTPError",
        "GatewayStreamError",
        "GatewayUnreachable",
        "SubscriptionHTTPError",
        "SubscriptionStreamError",
        "SubscriptionRateLimited",
        "SubscriptionEgressOffline",
        "SubscriptionUnreachable",
        "GatewayUpstreamOutOfCredit",
    }
)


#: Failures someone here can fix, told as soon as they happen rather than when
#: they pile up: the platform's own credential refused upstream (401/403), the
#: subscription egress being offline, and a provider account behind a gateway
#: row out of credit (`deploy/gateway/provider_credit.py`). Each can fail every
#: turn of a quiet deployment at a rate far below the burst threshold below,
#: indefinitely. The alert is keyed by `where` as well, which names the row for
#: an account out of credit: two empty accounts are two top-ups.
_ACTIONABLE_MODEL_FAILURES = frozenset(
    {"http_auth", "egress_offline", "provider_credit"}
)

#: How many model failures inside how long make an alert. One failed model call
#: is not news: across 2026-10-12..15 the gateway carried ~1,300 calls per 30
#: minutes and 0.75% of them failed — upstream overload (OpenAI "servers are
#: overloaded", DeepSeek "too busy") that Claude Code retries and that nobody
#: here can act on. Alerting each failure type once an hour made 334 alerts in
#: two weeks, which is how the outage alert of 2026-10-15 went unread.
#: Normal operation measured a median of 5 failures per 30 minutes, p95 33 and
#: p99 67 (≈ 2 / 11 / 22 per 10 minutes); our own gateway going down showed as
#: 50-120 refusals within one or two minutes, and losing the box's route abroad
#: as 35-96 per 30 minutes. 30 in 10 minutes is above the normal p99 rate and
#: is crossed within a minute by a gateway outage.
MODEL_FAILURE_BURST = 30
MODEL_FAILURE_WINDOW_S = 600.0


class _ModelFailureRate:
    """Recent model failures, to tell a blip from an outage."""

    def __init__(self) -> None:
        self._seen: list[tuple[float, str]] = []

    def over(self, exc_type: str, now: float) -> dict[str, int] | None:
        """Count this failure; the counts by type while the window holds at
        least the threshold, else ``None``. Every failure past it is handed on,
        not only the one that crossed it: the alerting module's repeat rules
        decide when an outage that goes on is news again, and they can only do
        that if they keep hearing of it."""
        cutoff = now - MODEL_FAILURE_WINDOW_S
        self._seen = [seen for seen in self._seen if seen[0] > cutoff]
        self._seen.append((now, exc_type))
        if len(self._seen) < MODEL_FAILURE_BURST:
            return None
        counts: dict[str, int] = {}
        for _, kind in self._seen:
            counts[kind] = counts.get(kind, 0) + 1
        return counts


# Process-local, owned by this module for the process's lifetime: an outage
# is minutes long, and a restart losing the window costs at most one window.
model_failures = _ModelFailureRate()


def _headline(err: BackendErrorIn) -> str:
    message = err.message.strip().splitlines()[0] if err.message.strip() else ""
    if len(message) > _CONTENT_MESSAGE_LIMIT:
        message = message[: _CONTENT_MESSAGE_LIMIT - 1] + "…"
    line = f"{err.exc_type}: {message}" if err.exc_type else message
    return line + _stream_note(err)


def event_content(err: BackendErrorIn) -> str:
    """The one line a human sees. The stack lives in ``meta`` — 芝士 reads the
    whole thing, a person reads this."""
    if err.where:
        return say("backendErrorAt", where=err.where, headline=_headline(err))
    return say("backendError", headline=_headline(err))


def summary_content(err: BackendErrorIn, count: int) -> str:
    minutes = int(DEDUP_WINDOW_S // 60)
    if err.where:
        return say(
            "backendErrorBurstAt",
            where=err.where,
            headline=_headline(err),
            minutes=minutes,
            count=count,
        )
    return say(
        "backendErrorBurst", headline=_headline(err), minutes=minutes, count=count
    )


def event_meta(err: BackendErrorIn, verdict: Verdict) -> dict:
    meta: dict = {"event_type": "backend_error", "fingerprint": fingerprint(err)}
    if err.exc_type:
        meta["exc_type"] = err.exc_type
    if err.stack:
        meta["stack"] = err.stack
    if err.where:
        meta["where"] = err.where
    if err.request_id:
        meta["request_id"] = err.request_id
    if err.received_events is not None:
        meta["received_events"] = err.received_events
    if err.upstream_failure:
        meta["upstream_failure"] = err.upstream_failure
    if err.ended_by:
        meta["ended_by"] = err.ended_by
    if verdict.kind == "summary":
        meta["summary"] = True
        meta["count"] = verdict.count
        meta["window_s"] = int(DEDUP_WINDOW_S)
    return meta


def from_exception(
    exc: BaseException,
    *,
    where: str | None = None,
    request_id: str | None = None,
) -> BackendErrorIn:
    """Build a report from a live exception. Secrets are scrubbed with the same
    filter that guards the log stream — a traceback can carry a token in a local
    or a repr, and these reports are readable by everyone in the room."""
    stack = "".join(traceback.format_exception(exc))
    message = str(exc) or type(exc).__name__
    return BackendErrorIn(
        message=scrub_secrets(message)[:1000],
        exc_type=type(exc).__name__[:200],
        # Keep the TAIL: the deepest frames and the exception line are the part
        # that identifies the bug; the outer middleware frames are boilerplate.
        stack=scrub_secrets(stack)[-8000:],
        where=where[:300] if where else None,
        request_id=request_id[:64] if request_id else None,
    )


_TOPIC_IN_PATH = re.compile(
    r"/topics?/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
    re.IGNORECASE,
)
_PROJECT_IN_PATH = re.compile(
    r"/projects?/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
    re.IGNORECASE,
)


def room_from_path(path: str) -> tuple[uuid.UUID | None, uuid.UUID | None]:
    """(project_id, topic_id) named by a request path, when it names one.

    This is how an in-process failure says where it happened: the URL that
    failed is the only context the middleware reliably has. A path that names
    neither (say ``/api/users/...``) is kept with no project.
    """
    topic = _TOPIC_IN_PATH.search(path)
    project = _PROJECT_IN_PATH.search(path)
    return (
        uuid.UUID(project.group(1)) if project else None,
        uuid.UUID(topic.group(1)) if topic else None,
    )


async def _where(
    db: AsyncSession, project_id: uuid.UUID | None, topic_id: uuid.UUID | None
) -> tuple[uuid.UUID | None, uuid.UUID | None]:
    """(project, conversation) the errors happened in, as far as either exists.
    A conversation of another project than the one named is not believed."""
    conversation = await db.get(Conversation, topic_id) if topic_id else None
    if conversation is not None and project_id in (None, conversation.project_id):
        return conversation.project_id, conversation.id
    if project_id is None:
        return None, None
    # Every project has a conversation (its 综合) from the moment it exists.
    known = await db.scalar(
        select(Conversation.project_id)
        .where(Conversation.project_id == project_id)
        .limit(1)
    )
    return known, None


def _record_meta(meta: dict, conversation_id: uuid.UUID | None) -> dict:
    if conversation_id is not None:
        meta["conversation"] = str(conversation_id)
    return meta


async def record(
    db: AsyncSession,
    *,
    project_id: uuid.UUID | None,
    topic_id: uuid.UUID | None,
    errors: list[BackendErrorIn],
) -> dict:
    """Admit `errors` and keep the survivors as run records.

    Returns ``{"accepted", "dropped"}``. Does NOT commit — the caller owns the
    transaction. A dropped duplicate is still a success for the reporter: it
    must never retry.
    """
    project, conversation = await _where(db, project_id, topic_id)
    accepted = 0
    for err in errors:
        # Counted before the dedup below: a repeat of the same failure is
        # still one more failed call, and the rate is what this alert reads.
        if err.exc_type in _MODEL_FAILURE_TYPES:
            where = [
                f"项目：{project}",
                f"对话：{conversation}",
                f"请求：{err.request_id or 'unknown'}",
            ]
            if err.upstream_failure in _ACTIONABLE_MODEL_FAILURES:
                alerting.send(
                    "模型请求失败",
                    [scrub_secrets(err.message) + _stream_note(err), *where],
                    key=f"model-actionable:{err.exc_type}:{err.upstream_failure}:{err.where or ''}",
                )
            counts = model_failures.over(err.exc_type, time.time())
            if counts is not None:
                total = sum(counts.values())
                by_type = "，".join(f"{kind} {n}" for kind, n in sorted(counts.items()))
                alerting.send(
                    "模型请求大量失败",
                    [
                        f"{int(MODEL_FAILURE_WINDOW_S / 60)} 分钟内失败 {total} 次"
                        f"（阈值 {MODEL_FAILURE_BURST}）：{by_type}",
                        f"最近一次：{scrub_secrets(err.message)}{_stream_note(err)}",
                        *where,
                    ],
                    # Keyed by the kind of failure that dominates the window,
                    # not one key for every burst: an upstream-overload spike
                    # (stream cuts) must not hold back, under its repeat window,
                    # the gateway outage (unreachable) that follows it.
                    key=f"model-failure-burst:{max(counts, key=counts.__getitem__)}",
                )
        verdict = intake.admit(
            str(project or ""),
            fingerprint(err),
            sample=err,
            topic_id=conversation,
            project_uuid=project,
        )
        if not verdict:
            continue
        content = (
            summary_content(err, verdict.count)
            if verdict.kind == "summary"
            else event_content(err)
        )
        await keep_record(
            db,
            project_id=project,
            conversation_id=None,
            content=content,
            meta=_record_meta(event_meta(err, verdict), conversation),
        )
        accepted += 1
    return {"accepted": accepted, "dropped": len(errors) - accepted}


async def flush_expired(now: float | None = None, *, force: bool = False) -> int:
    """Keep the summary record for every burst whose window has closed.

    Returns how many records were written. Reads the session factory off the
    module at call time so a test can point it at its own database.
    """
    bursts = intake.sweep(now, force=force)
    if not bursts:
        return 0
    async with async_session_factory() as session:
        for burst in bursts:
            await keep_record(
                session,
                project_id=burst.project_uuid,
                conversation_id=None,
                content=summary_content(burst.sample, burst.count),
                meta=_record_meta(
                    event_meta(burst.sample, Verdict("summary", burst.count)),
                    burst.topic_id,
                ),
            )
        await session.commit()
    return len(bursts)


async def flush_all() -> int:
    """Keep the summary of EVERY window this process still holds, full or not.

    For the one caller that is about to stop existing: a handover stops the
    periodic job that would have flushed these windows (`backend error flush`)
    and then this process goes, taking everything `intake` still had in memory
    with it. The window being young is not a reason to drop it — nobody will
    ever look at it again. Same bookkeeping as ``flush_expired``; the count of
    occurrences is what makes the record worth keeping.
    """
    return await flush_expired(force=True)


def register_periodic_jobs(jobs: PeriodicJobs) -> None:
    jobs.register(
        "backend error flush", settings.backend_error_flush_interval_s, flush_expired
    )


async def report_request_failure(
    exc: BaseException, *, method: str, path: str, request_id: str | None = None
) -> bool:
    """This app reporting ITSELF: the middleware hands over an exception that
    escaped a request, and it is kept with the project and conversation the
    request's path names, if any.

    Best-effort in the strongest sense — it is running on the failure path of a
    request that is already broken, so every step swallows its own errors and
    the original exception is never disturbed. Returns True iff a record was
    written (tests and callers use it; nothing branches on it in production).

    The intake is consulted BEFORE the database is touched, so a route failing
    thousands of times a minute costs one in-memory dict lookup per failure, not
    a connection.
    """
    try:
        project_id, topic_id = room_from_path(path)
        err = from_exception(exc, where=f"{method} {path}", request_id=request_id)
        async with async_session_factory() as session:
            result = await record(
                session, project_id=project_id, topic_id=topic_id, errors=[err]
            )
            if not result["accepted"]:
                return False
            await session.commit()
            return True
    except Exception:  # reporting a failure must never add one
        logger.warning("backend error report failed for %s %s", method, path)
        return False
