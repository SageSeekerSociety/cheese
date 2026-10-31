"""Find N+1 reads inside one request, the way prosopite does.

An N+1 is not "the same SQL ran twice"; it is the same *shape* of SQL issued
over and over from the *same place*. A page that reads one topic row once and
renders it from four places runs the identical statement more than once and is
not an N+1. A loop that asks for one row per member runs statements that differ
only in the bound value, and that is one. So a statement is grouped under two
things at once — the call site that sent it and its fingerprint — and a group is
reported once its count reaches ``min_n_queries`` (2 by default, as in
prosopite).

The fingerprint replaces everything a caller varies with ``?``: numbers, quoted
strings, dollar-quoted strings and driver placeholders; an ``IN`` list collapses
to a single ``?``. Double-quoted names are left alone on purpose: PostgreSQL
reads them as identifiers, so folding ``"topics"`` and ``"members"`` into one
shape would merge two unrelated queries.

Where the request begins and ends is a ContextVar, not a thread-local. One
worker thread runs many requests concurrently, so thread-local state would mix
them; ``contextvars`` follows the task, and SQLAlchemy's greenlet carries the
context into the blocking part of an async execute (checked against
SQLAlchemy 2.0.50 + greenlet 3.5.1). Every session a request opens — the write
path opens more than one — records into the same watch, because the watch is
per task and not per session.

The engine hook is ``before_cursor_execute`` on ``engine.sync_engine``, the one
entry point an async engine exposes (``core.db`` hangs its pool warnings off the
same place). The callback is synchronous and runs in the greenlet: it may not
await, so it only records. Grouping and logging happen when the request is over
(``finish``), which is also where the whitelists are applied — a suppressed
group still costs its count, it is only left out of the log line.

This module only logs. It never raises from a recording or a report, and it
never raises because of what it found: a detector that turns a slow query into a
failed request would be worse than the extra query. ``raise`` is a later step,
once a week of dev logs has made the whitelist trustworthy.

Boundary: ``QueryWatchMiddleware`` (pure ASGI, so a streamed response is still
inside the request when its queries run) wraps one HTTP request; ``begin`` /
``finish`` are the same pair for callers that are not an HTTP request.

Whitelists have two dimensions, both substring matches:

* ``IGNORED_STACKS`` — any frame of the call site contains one of these (a
  module path, a function name).
* ``IGNORED_SQL`` — the whitespace-normalized statement contains one of these,
  case-insensitively.

Both start empty. Known-legitimate repeats are added here as the dev logs show
them, not pre-emptively.
"""

from __future__ import annotations

import logging
import re
import sys
import sysconfig
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass

logger = logging.getLogger(__name__)

#: Report a (call site, fingerprint) group once it ran this many times.
DEFAULT_MIN_N_QUERIES = 2

#: Legitimate repeats, by call site. A frame matching any entry exempts the
#: group. Write paths the way ``call_site`` writes them, below the package
#: directory: ``domain/topic/repositories/reads.py``. Both layouts trim to
#: that — the image keeps the package at ``/app/app/...`` and a checkout at
#: ``backend/app/...`` — so an ``app/domain/...`` prefix matches nothing.
IGNORED_STACKS: tuple[str, ...] = (
    # 芝士 waiting on its card looks at it once a second while the person
    # decides (``assistant_agent.WAIT_S``): the repeat is the wait.
    " agent_wait",
)

#: Legitimate repeats, by statement. Case-insensitive substring of the
#: whitespace-normalized SQL (``select ... from audit_log``).
IGNORED_SQL: tuple[str, ...] = ()

#: How much of the statement the group keeps for the log line.
SQL_PREVIEW_CHARS = 400

#: How many non-library frames name a call site. Enough to tell two repository
#: methods apart, short enough that logging one is still one line.
STACK_FRAMES = 6

#: A string literal in single quotes, with '' and backslash escapes.
_SINGLE_QUOTED = re.compile(r"'(?:[^'\\]|\\.|'')*'")
#: A PostgreSQL dollar-quoted string: $$...$$ or $tag$...$tag$.
_DOLLAR_QUOTED = re.compile(r"\$[A-Za-z_0-9]*\$.*?\$[A-Za-z_0-9]*\$", re.DOTALL)
#: Driver placeholders: asyncpg's $1 and SQLAlchemy's %(name)s.
_PLACEHOLDER = re.compile(r"\$\d+|%\([^)]+\)s")
#: A number not glued to an identifier (``cell_2fa`` and ``users2`` stay).
_NUMBER = re.compile(r"(?<![\w.])[-+]?\d+(?:\.\d+)?(?![\w.])")
#: An IN list of placeholders, whatever its length.
_IN_LIST = re.compile(r"\bin\s*\(\s*\?(?:\s*,\s*\?)*\s*\)", re.IGNORECASE)
_WHITESPACE = re.compile(r"\s+")

#: Where the current request's watch lives. ``None`` outside a request: work
#: that is not a request (startup, background jobs, migrations) is not watched,
#: because there is no end at which a count means anything.
_current: ContextVar[RequestWatch | None] = ContextVar("query_watch", default=None)

_STDLIB = sysconfig.get_paths()["stdlib"]
_THIS_FILE = __file__

#: Marks an engine whose events this module already listens to, so importing
#: the app twice (tests do) does not record every statement twice.
_INSTALLED = "_cheese_query_watch_installed"


def fingerprint_sql(statement: str) -> str:
    """The statement with everything a caller varies replaced by ``?``."""
    shape = _DOLLAR_QUOTED.sub("?", statement)
    shape = _SINGLE_QUOTED.sub("?", shape)
    shape = _PLACEHOLDER.sub("?", shape)
    shape = _NUMBER.sub("?", shape)
    shape = _IN_LIST.sub("in (?)", shape)
    return _WHITESPACE.sub(" ", shape).strip()


def _normalized(statement: str) -> str:
    """The statement for whitelist matching: whitespace collapsed, lowercased."""
    return _WHITESPACE.sub(" ", statement).strip().lower()


def group_key(stack: tuple[str, ...], fingerprint: str) -> tuple[tuple[str, ...], str]:
    """Two statements are one repeat only when the call site and shape match."""
    return (stack, fingerprint)


def _is_library(path: str) -> bool:
    return "site-packages" in path or path.startswith(_STDLIB) or path.startswith("<")


def _short_path(path: str) -> str:
    # The image keeps the package at /app/app/... and a checkout at
    # backend/app/...; keep the part below the last app/ so a log line (and a
    # stack whitelist entry) does not depend on where the tree was built.
    return path.rsplit("/app/", 1)[-1]


def call_site(limit: int = STACK_FRAMES) -> tuple[str, ...]:
    """The innermost ``limit`` non-library frames that sent a statement.

    An async execute runs its blocking half in a greenlet SQLAlchemy spawned;
    that greenlet's own stack stops at SQLAlchemy, and the caller is the parent
    greenlet's suspended stack (``core.db._taken_at`` reads a pool checkout the
    same way). Only code locations are kept — the frame objects are not — so a
    stack costs a short walk and no formatting until a group is reported.
    """
    frame = sys._getframe(1)
    try:
        # The offline guard imports this module without sqlalchemy or greenlet,
        # and only then misses the parent-greenlet hop.
        from greenlet import getcurrent  # deferred-import: ships with sqlalchemy

        parent = getcurrent().parent
        if parent is not None and parent.gr_frame is not None:
            frame = parent.gr_frame
    except Exception:  # pragma: no cover - no greenlet means the plain stack
        pass
    found: list[str] = []
    while frame is not None and len(found) < limit:
        path = frame.f_code.co_filename
        if path != _THIS_FILE and not _is_library(path):
            found.append(f"{_short_path(path)}:{frame.f_lineno} {frame.f_code.co_name}")
        frame = frame.f_back
    return tuple(found)


@dataclass(frozen=True)
class Repeated:
    """One reported group: a call site that sent the same shape twice or more."""

    count: int
    stack: tuple[str, ...]
    fingerprint: str
    sql: str


@dataclass
class _Group:
    stack: tuple[str, ...]
    fingerprint: str
    sql: str
    normalized: str
    count: int = 1


class RequestWatch:
    """The statements one request sent, grouped by call site and shape.

    ``note`` is called from the engine callback and only touches a dict; what
    the groups mean is decided by ``repeats`` at the end of the request.
    """

    __slots__ = ("closed", "db_s", "groups", "statements")

    def __init__(self) -> None:
        self.groups: dict[tuple[tuple[str, ...], str], _Group] = {}
        #: How many statements ran and how long they held the database, for
        #: the response's Server-Timing (``db``): what a slow request spent
        #: waiting on the database rather than in Python.
        self.statements = 0
        self.db_s = 0.0
        #: Set when the request is over. A background task that inherited this
        #: watch must not keep appending to a count nobody will report.
        self.closed = False

    def note(self, statement: str, *, stack: Sequence[str] | None = None) -> None:
        """Record one execution of ``statement`` from ``stack`` (or here)."""
        if self.closed:
            return
        try:
            shape = fingerprint_sql(statement)
            where = tuple(stack) if stack is not None else call_site()
            key = group_key(where, shape)
            group = self.groups.get(key)
            if group is None:
                self.groups[key] = _Group(
                    stack=where,
                    fingerprint=shape,
                    sql=statement[:SQL_PREVIEW_CHARS],
                    normalized=_normalized(statement),
                )
            else:
                group.count += 1
        except Exception:
            # A detector that breaks a request is worse than the query it
            # failed to count. Keep the request, leave a trail for a human.
            logger.debug("query watch could not record a statement", exc_info=True)

    def repeats(
        self,
        *,
        min_n_queries: int = DEFAULT_MIN_N_QUERIES,
        ignore_stacks: Sequence[str] = IGNORED_STACKS,
        ignore_sql: Sequence[str] = IGNORED_SQL,
    ) -> list[Repeated]:
        """The groups worth a log line, most frequent first."""
        found: list[Repeated] = []
        for group in self.groups.values():
            if group.count < min_n_queries:
                continue
            if any(word in frame for word in ignore_stacks for frame in group.stack):
                continue
            lowered = [word.lower() for word in ignore_sql]
            if any(word in group.normalized for word in lowered):
                continue
            found.append(
                Repeated(
                    count=group.count,
                    stack=group.stack,
                    fingerprint=group.fingerprint,
                    sql=group.sql,
                )
            )
        found.sort(key=lambda item: (-item.count, item.fingerprint))
        return found


def report(
    watch: RequestWatch,
    *,
    min_n_queries: int = DEFAULT_MIN_N_QUERIES,
    ignore_stacks: Sequence[str] = IGNORED_STACKS,
    ignore_sql: Sequence[str] = IGNORED_SQL,
) -> list[Repeated]:
    """Log the repeated groups of a finished request; never raises."""
    try:
        repeats = watch.repeats(
            min_n_queries=min_n_queries,
            ignore_stacks=ignore_stacks,
            ignore_sql=ignore_sql,
        )
        for item in repeats:
            logger.warning(
                "possible N+1: %d statements with one shape from one call site "
                "(%s); first: %s",
                item.count,
                " < ".join(item.stack[:3]) or "unknown",
                item.sql,
            )
        return repeats
    except Exception:
        logger.debug("query watch could not report a request", exc_info=True)
        return []


def begin() -> Token[RequestWatch | None]:
    """Start watching; pair with ``finish`` on the same context."""
    return _current.set(RequestWatch())


def finish(token: Token[RequestWatch | None]) -> list[Repeated]:
    """Stop watching the request ``begin`` started and log what it saw."""
    watch = _current.get()
    _current.reset(token)
    if watch is None:
        return []
    watch.closed = True
    return report(watch)


def current_watch() -> RequestWatch | None:
    """The watch of the request this code runs in, if it is inside one."""
    return _current.get()


@contextmanager
def request_scope() -> Iterator[RequestWatch]:
    """``begin`` / ``finish`` as a context manager, for non-ASGI callers."""
    watch = RequestWatch()
    token = _current.set(watch)
    try:
        yield watch
    finally:
        _current.reset(token)
        watch.closed = True
        report(watch)


def _on_before_cursor_execute(
    _conn, _cursor, statement, _parameters, context, _executemany
) -> None:
    watch = _current.get()
    if watch is not None:
        watch.note(statement)
        if context is not None:
            context._cheese_started = time.perf_counter()


def _on_after_cursor_execute(
    _conn, _cursor, _statement, _parameters, context, _executemany
) -> None:
    watch = _current.get()
    started = getattr(context, "_cheese_started", None)
    if watch is not None and started is not None and not watch.closed:
        watch.statements += 1
        watch.db_s += time.perf_counter() - started


def watch_engine(engine) -> None:
    """Record every statement ``engine`` executes while a request is open.

    Idempotent: listening twice would count every statement twice and make the
    counts mean nothing.
    """
    # The offline guard imports this module without the backend's dependencies.
    from sqlalchemy import event  # deferred-import: guard runs without it

    sync_engine = engine.sync_engine
    if getattr(sync_engine, _INSTALLED, False):
        return
    event.listen(sync_engine, "before_cursor_execute", _on_before_cursor_execute)
    event.listen(sync_engine, "after_cursor_execute", _on_after_cursor_execute)
    setattr(sync_engine, _INSTALLED, True)


class QueryWatchMiddleware:
    """One watch per HTTP request, across every session that request opens.

    Pure ASGI rather than a ``BaseHTTPMiddleware``: the latter returns as soon
    as the response starts, so queries a streaming handler makes while it
    streams would fall outside the request.
    """

    def __init__(
        self,
        app,
        *,
        min_n_queries: int = DEFAULT_MIN_N_QUERIES,
        ignore_stacks: Sequence[str] = IGNORED_STACKS,
        ignore_sql: Sequence[str] = IGNORED_SQL,
    ) -> None:
        self.app = app
        self.min_n_queries = min_n_queries
        self.ignore_stacks = tuple(ignore_stacks)
        self.ignore_sql = tuple(ignore_sql)

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        watch = RequestWatch()
        token = _current.set(watch)

        async def timed_send(message) -> None:
            # What the request spent on the database, beside the app's own
            # total (``app;dur``, set where the request is logged): the two
            # tell a slow query from slow Python. A streamed body's later
            # queries come after this header and are not in it.
            if message["type"] == "http.response.start":
                timing = (
                    f'db;dur={watch.db_s * 1000:.1f};desc="{watch.statements} sql"'
                ).encode()
                message = {
                    **message,
                    "headers": [
                        *message.get("headers", ()),
                        (b"server-timing", timing),
                    ],
                }
            await send(message)

        try:
            await self.app(scope, receive, timed_send)
        finally:
            _current.reset(token)
            watch.closed = True
            report(
                watch,
                min_n_queries=self.min_n_queries,
                ignore_stacks=self.ignore_stacks,
                ignore_sql=self.ignore_sql,
            )


def install(app, engine=None) -> None:
    """Hang the detector off ``app``: engine events plus the request middleware.

    ``engine`` defaults to ``core.db.engine``; it is a parameter so a test can
    mount the middleware without building the app.
    """
    if engine is None:
        # The offline guard imports this module without the backend stack.
        from app.core.db import engine as db_engine  # deferred-import: built on import

        engine = db_engine
    watch_engine(engine)
    app.add_middleware(QueryWatchMiddleware)


__all__ = [
    "DEFAULT_MIN_N_QUERIES",
    "IGNORED_SQL",
    "IGNORED_STACKS",
    "QueryWatchMiddleware",
    "Repeated",
    "RequestWatch",
    "begin",
    "call_site",
    "current_watch",
    "fingerprint_sql",
    "finish",
    "group_key",
    "install",
    "report",
    "request_scope",
    "watch_engine",
]
