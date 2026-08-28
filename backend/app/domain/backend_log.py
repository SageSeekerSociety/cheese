"""Backend error intake → 现场 event blocks (the mirror of ``frontend_log``).

Same reason, other half of the stack: the dogfooding debugger is usually an
AGENT, and an agent can no more read ``docker logs`` than it can read a user's
browser console. ``core.obs`` renders structlog to stdout and stops there — in
production that ends up inside a container, in dev inside a file on the host,
and 现场 can reach neither. So an unhandled backend exception must land
somewhere the platform can *query*: a ``kind=event`` block (``meta.event_type =
"backend_error"``) on the topic the request belonged to.

**Push, not pull.** Errors are POSTed in (by the process that raised them, or by
this app's own middleware), exactly like the frontend reporter. The alternative
— giving 芝士 a general "read the logs" capability — is not a style choice we
declined: a user project's 分身 would reach the platform's own logs through that
same opening. A push channel exposes no such handle. See #188 for where this
channel's remit stops: it receives reports, nothing else.

**The whole difficulty is granularity, not plumbing.** A room is a conversation,
not a monitoring dashboard, so the defaults below are tuned for PRODUCTION
volume (one broken route can raise thousands of times a minute), not for the
once-per-bug rhythm of development:

- Only *unhandled* failures arrive here. An expected 4xx is normal flow.
- A fingerprint is reported **once per window**; repeats are counted in silence.
- A burst collapses into ONE summary block ("这个错误 5 分钟内 800 次") when its
  window closes — never one block per occurrence.
- A per-project **hourly hard cap** silently drops everything beyond it.

Deliberate under-reporting: anything past the hourly cap is gone, and a
fingerprint-storm large enough to hit `_WINDOW_PRUNE_AT` forfeits the summaries
it drops. That is the intended trade — 宁可漏报也不能刷屏.

What is NOT traded away is the COUNT. A burst used to get its summary only from
the next occurrence of the same fingerprint, so the commonest case — a bug you
fixed, which by definition stops recurring — kept the first detail line and
silently lost "it happened 500 times", usually the number that says how bad it
was. `BackendErrorFlushRunner` ticks `flush_expired` so a window closes on time
instead of on the next failure. Late, never absent.

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
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import async_session_factory
from app.core.obs import scrub_secrets
from app.domain.block.models import AuthorType, BlockKind
from app.domain.block.repositories import BlockRepository
from app.domain.project.repositories import ProjectRepository
from app.domain.topic.repositories import TopicRepository

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


class BackendErrorBatchIn(BaseModel):
    # Both optional: a scoped X-Cheese-Token already names the room, and the
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
    project_uuid: uuid.UUID
    topic_id: uuid.UUID


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

    def sweep(self, now: float | None = None) -> list[ExpiredBurst]:
        """Close every expired window, whether or not its error ever came back.

        Without this, a burst's summary was emitted only by the NEXT occurrence
        of the same fingerprint — and the most common shape of a bug you just
        fixed is that there is no next occurrence. The room would keep the first
        detail line and silently lose the fact that it happened 500 times, which
        is usually the number that tells you how bad it was.

        Reporting late is fine here; reporting never is not.
        """
        now = time.time() if now is None else now
        bursts: list[ExpiredBurst] = []
        for key, window in list(self._windows.items()):
            if now - window.opened_at < DEDUP_WINDOW_S:
                continue
            del self._windows[key]
            if window.count <= 1:
                # It was reported in full when it opened; "1 次" adds nothing.
                continue
            if (
                window.sample is None
                or window.topic_id is None
                or window.project_uuid is None
            ):
                continue  # opened without a room to write back into
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


def _headline(err: BackendErrorIn) -> str:
    message = err.message.strip().splitlines()[0] if err.message.strip() else ""
    if len(message) > _CONTENT_MESSAGE_LIMIT:
        message = message[: _CONTENT_MESSAGE_LIMIT - 1] + "…"
    return f"{err.exc_type}: {message}" if err.exc_type else message


def event_content(err: BackendErrorIn) -> str:
    """The one line a human sees. The stack lives in ``meta`` — 芝士 reads the
    whole thing, a person reads this."""
    where = f"（{err.where}）" if err.where else ""
    return f"后端报错{where}：{_headline(err)}"


def summary_content(err: BackendErrorIn, count: int) -> str:
    minutes = int(DEDUP_WINDOW_S // 60)
    where = f"（{err.where}）" if err.where else ""
    return f"后端报错刷屏{where}：{_headline(err)} —— {minutes} 分钟内 {count} 次"


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

    This is how an in-process failure finds its room: the URL that failed is the
    only context the middleware reliably has. A path that names neither (say
    ``/api/users/...``) has no 现场 to report into — the caller drops it rather
    than guessing, exactly as the browser reporter drops errors raised outside a
    project.
    """
    topic = _TOPIC_IN_PATH.search(path)
    project = _PROJECT_IN_PATH.search(path)
    return (
        uuid.UUID(project.group(1)) if project else None,
        uuid.UUID(topic.group(1)) if topic else None,
    )


async def _target_topic(
    db: AsyncSession, project_id: uuid.UUID | None, topic_id: uuid.UUID | None
):  # noqa: ANN202 — Topic model, imported lazily by the repositories
    """The topic these errors belong on: the one named, else the project's root
    topic (mirrors the frontend reporter's fallback). None = nowhere to put them."""
    topics = TopicRepository(db)
    topic = await topics.get(topic_id) if topic_id else None
    if topic is not None and (project_id is None or topic.project_id == project_id):
        return topic
    if project_id is None:
        return None
    project = await ProjectRepository(db).get(project_id)
    if project is None or project.root_topic_id is None:
        return None
    return await topics.get(project.root_topic_id)


async def record(
    db: AsyncSession,
    *,
    project_id: uuid.UUID | None,
    topic_id: uuid.UUID | None,
    errors: list[BackendErrorIn],
) -> dict | None:
    """Admit `errors` and write the survivors as 现场 event blocks.

    Returns ``{"accepted", "dropped"}``, or ``None`` when there is no topic to
    attach to. Does NOT commit — the caller owns the transaction. A dropped
    duplicate is still a success for the reporter: it must never retry.
    """
    topic = await _target_topic(db, project_id, topic_id)
    if topic is None:
        return None

    blocks = BlockRepository(db)
    accepted = 0
    for err in errors:
        verdict = intake.admit(
            str(topic.project_id),
            fingerprint(err),
            sample=err,
            topic_id=topic.id,
            project_uuid=topic.project_id,
        )
        if not verdict:
            continue
        content = (
            summary_content(err, verdict.count)
            if verdict.kind == "summary"
            else event_content(err)
        )
        await blocks.add(
            project_id=topic.project_id,
            topic_id=topic.id,
            author="backend",
            author_type=AuthorType.system,
            content=content,
            kind=BlockKind.event,
            meta=event_meta(err, verdict),
        )
        accepted += 1
    return {"accepted": accepted, "dropped": len(errors) - accepted}


async def flush_expired(now: float | None = None) -> int:
    """Write the summary line for every burst whose window has closed.

    Returns how many blocks were written. Reads the session factory off the
    module at call time so a test can point it at its own database.
    """
    bursts = intake.sweep(now)
    if not bursts:
        return 0
    async with async_session_factory() as session:
        blocks = BlockRepository(session)
        for burst in bursts:
            await blocks.add(
                project_id=burst.project_uuid,
                topic_id=burst.topic_id,
                author="backend",
                author_type=AuthorType.system,
                content=summary_content(burst.sample, burst.count),
                kind=BlockKind.event,
                meta=event_meta(burst.sample, Verdict("summary", burst.count)),
            )
        await session.commit()
    return len(bursts)


async def report_request_failure(
    exc: BaseException, *, method: str, path: str, request_id: str | None = None
) -> bool:
    """This app reporting ITSELF: the middleware hands over an exception that
    escaped a request, and it lands in that request's room.

    Best-effort in the strongest sense — it is running on the failure path of a
    request that is already broken, so every step swallows its own errors and
    the original exception is never disturbed. Returns True iff a block was
    written (tests and callers use it; nothing branches on it in production).

    The intake is consulted BEFORE the database is touched, so a route failing
    thousands of times a minute costs one in-memory dict lookup per failure, not
    a connection.
    """
    try:
        project_id, topic_id = room_from_path(path)
        if project_id is None and topic_id is None:
            # No room named by the URL — nowhere to report. Same call the browser
            # reporter makes outside a project: drop rather than guess.
            return False
        err = from_exception(exc, where=f"{method} {path}", request_id=request_id)
        async with async_session_factory() as session:
            result = await record(
                session, project_id=project_id, topic_id=topic_id, errors=[err]
            )
            if result is None or not result["accepted"]:
                return False
            await session.commit()
            return True
    except Exception:  # noqa: BLE001 — reporting a failure must never add one
        logger.warning("backend error report failed for %s %s", method, path)
        return False
