"""Async SQLAlchemy engine, session factory, and declarative base."""

import json
import logging
import os
import sys
import sysconfig
import time
from collections.abc import AsyncGenerator, Callable
from contextlib import AbstractAsyncContextManager
from typing import Any

import greenlet
from fastapi import HTTPException
from sqlalchemy import event, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Session, SessionTransaction
from sqlalchemy.pool import NullPool, QueuePool

from app.core.config import settings
from app.core.sentences import say

logger = logging.getLogger(__name__)

# Fusion merge (A6): ONE declarative Base / metadata across the whole app so
# cross-domain FKs resolve (e.g. cheesex agent_bindings.user_id -> main's user).
# cheesex models import Base from here; main's models import the same class from
# app.db.base_class — they are now the SAME registry.
from app.db.base_class import Base  # noqa: E402  (re-exported)

# Accept sync-style URLs (postgresql:// / +psycopg2) and normalize to asyncpg —
# some environments configure DATABASE_URL in the sync form (carried over from
# app.db.session, which used to build its own engine from the same setting).
_db_url = settings.database_url
if _db_url.startswith("postgresql://"):
    _db_url = _db_url.replace("postgresql://", "postgresql+asyncpg://", 1)
elif _db_url.startswith("postgresql+psycopg2://"):
    _db_url = _db_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)

# CHEESEX_TEST_NULLPOOL, set before any app import, gives this process a
# NullPool (a fresh connection per checkout) instead of the QueuePool below.
# Only a test's own child process sets it; the pytest suite runs the
# production pool shape and closes each event loop's connections itself
# (tests/conftest.py, tests/integration/conftest.py).
if os.environ.get("CHEESEX_TEST_NULLPOOL"):
    _engine_kwargs: dict[str, Any] = {"poolclass": NullPool}
else:
    # `db_pool_size` / `db_max_overflow` are the CONTAINER's budget, and this is
    # one of its workers (`backend/Dockerfile` passes the same BACKEND_WORKERS
    # to uvicorn's --workers), so it takes its share of them.
    _pool_size, _pool_overflow = settings.db_connections_per_worker()
    _engine_kwargs = {
        "pool_size": _pool_size,
        "max_overflow": _pool_overflow,
        "pool_timeout": settings.db_pool_timeout_s,
        "pool_pre_ping": settings.db_pool_pre_ping,
    }


def _json_dumps_utf8(obj: Any) -> str:
    # Send JSON/JSONB binds as raw UTF-8, not \uXXXX escapes. json.dumps
    # defaults to ensure_ascii=True, and PostgreSQL rejects non-ASCII \u
    # escapes in jsonb unless the server encoding is UTF8 — the dev box's
    # database was initdb'd as SQL_ASCII, so the first GitHub profile with a
    # Chinese display name (raw_profile jsonb) 500'd the OAuth callback
    # (2026-08-10, #222). Raw UTF-8 bytes pass under either encoding, and are
    # what ends up stored anyway.
    return json.dumps(obj, ensure_ascii=False)


engine = create_async_engine(
    _db_url,
    echo=settings.db_echo,
    json_serializer=_json_dumps_utf8,
    **_engine_kwargs,
)
async_session_factory = async_sessionmaker(engine, expire_on_commit=False)

#: How long a readiness probe gives a dependency, to connect and to answer.
#: Under the 3 s the rollout's curl waits, so a dead dependency is answered as
#: a 503 rather than as a timeout.
PROBE_TIMEOUT_S = 2.0

# The readiness probe's own way in: one fresh connection per probe, never one
# from the pool above. Readiness asks "can this process reach the database";
# borrowing from the pool would instead ask "is the pool free right now", and
# a pool drained by the reconnect wave after a release switch would then read
# as an outage — the rollout would fail a backend that is serving. How full
# the pool is stays in the report as `pool`, for a human, never as a verdict.
probe_engine = create_async_engine(
    _db_url,
    poolclass=NullPool,
    connect_args={"timeout": PROBE_TIMEOUT_S, "command_timeout": PROBE_TIMEOUT_S},
)


def pool_status(target: AsyncEngine | None = None) -> dict[str, int] | None:
    """How much of a process's pool is in use right now; None under NullPool."""
    pool = (target or engine).pool
    if not isinstance(pool, QueuePool):
        return None
    return {
        "size": pool.size(),
        "max_overflow": pool._max_overflow,
        "checked_out": pool.checkedout(),
        "overflow": max(pool.overflow(), 0),
    }


#: A connection held longer than this is named, with where it was taken: the
#: saturation line alone says the pool ran dry, never who was holding it.
LONG_HOLD_S = 0.5
_STDLIB = sysconfig.get_paths()["stdlib"]


def _taken_at(limit: int = 4) -> tuple[str, ...]:
    """The innermost non-library frames of the code taking a connection.

    An async checkout runs in a greenlet SQLAlchemy spawned, whose own stack
    stops at SQLAlchemy; the caller is the parent greenlet's suspended stack.
    Only code locations are kept, so a checkout costs a short walk and no
    formatting; they are formatted only for a hold worth reporting.
    """
    frame = sys._getframe(1)
    parent = greenlet.getcurrent().parent
    if parent is not None and parent.gr_frame is not None:
        frame = parent.gr_frame
    found: list[str] = []
    while frame is not None and len(found) < limit:
        path = frame.f_code.co_filename
        if "site-packages" not in path and not path.startswith(_STDLIB):
            found.append(
                f"{path.rsplit('/app/', 1)[-1]}:{frame.f_lineno} {frame.f_code.co_name}"
            )
        frame = frame.f_back
    return tuple(found)


def watch_pool(
    target: AsyncEngine,
    *,
    every_seconds: float = 60,
    long_hold_s: float = LONG_HOLD_S,
) -> None:
    """Say when the pool runs dry, and who held a connection too long.

    A checkout that takes the pool's last slot is the moment the next request
    starts waiting on db_pool_timeout_s; it is logged once per interval with
    the connections held longest and where each was taken. A connection held
    longer than ``long_hold_s`` is logged when it comes back, with where it was
    taken, so a saturation can be traced to the code that caused it.
    """
    logged_at = 0.0
    holders: dict[int, tuple[float, tuple[str, ...]]] = {}

    @event.listens_for(target.sync_engine, "checkout")
    def _on_checkout(dbapi_connection, connection_record, connection_proxy):
        nonlocal logged_at
        now = time.monotonic()
        holders[id(connection_record)] = (now, _taken_at())
        status = pool_status(target)
        if status is None:
            return
        ceiling = status["size"] + status["max_overflow"]
        if status["checked_out"] < ceiling:
            return
        if now - logged_at < every_seconds:
            return
        logged_at = now
        longest = sorted(holders.values())[:3]
        logger.warning(
            "database pool saturated: %d/%d connections checked out; the next "
            "request waits up to %.0fs (raise db_pool_size or find what holds "
            "them); held longest: %s",
            status["checked_out"],
            ceiling,
            settings.db_pool_timeout_s,
            "; ".join(
                f"{(now - since) * 1000:.0f} ms by {' < '.join(at) or '?'}"
                for since, at in longest
            ),
        )

    @event.listens_for(target.sync_engine, "checkin")
    def _on_checkin(dbapi_connection, connection_record):
        taken = holders.pop(id(connection_record), None)
        if taken is None:
            return
        since, at = taken
        held = time.monotonic() - since
        if held > long_hold_s:
            logger.warning(
                "database connection held %.0f ms by %s",
                held * 1000,
                " < ".join(at) or "?",
            )


watch_pool(engine)


# What every caller actually does with one: `async with sessions() as session`.
# Spelled as `Callable[[], AsyncSession]` it type-checks against nothing useful,
# because the thing returned is a context manager, not a session.
SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]

__all__ = [
    "AsyncSession",
    "Base",
    "SessionFactory",
    "apply_migration_timeouts",
    "async_session_factory",
    "engine",
    "get_db",
]


def apply_migration_timeouts(connection: Connection) -> None:
    """Bound each migration's lock wait and total run time (#356).

    Called once by alembic's ``env.py`` on the single connection an
    ``upgrade head`` uses, before the first migration. Each migration then runs
    in its own transaction on that connection (``transaction_per_migration``),
    so the settings must outlive a commit: ``set_config(name, value,
    is_local=false)`` is a parameterizable, injection-safe equivalent of
    session-scoped ``SET``, and so protects every migration in the run.

    Why it matters: a migration whose ``ALTER TABLE`` needs an ACCESS EXCLUSIVE
    lock will otherwise wait indefinitely behind a live backend's open
    transaction. In #356 that wait consumed the deploy's entire 30-minute budget
    and browned out cheese-dev's only runner slot. A short ``lock_timeout`` turns
    that into a fast, retryable failure; ``statement_timeout`` defaults to 0
    (unlimited) so a genuinely long table rewrite is never killed mid-migration.
    """
    connection.execute(
        text("SELECT set_config('lock_timeout', :value, false)"),
        {"value": settings.migration_lock_timeout},
    )
    connection.execute(
        text("SELECT set_config('statement_timeout', :value, false)"),
        {"value": settings.migration_statement_timeout},
    )


#: SQLSTATEs a migration's transaction ends with when it lost a race for a lock
#: against live traffic: ``lock_not_available`` (its ``lock_timeout`` ran out)
#: and ``deadlock_detected`` (Postgres chose it as the deadlock's victim).
_LOCK_CONTENTION = frozenset({"55P03", "40P01"})


class MigrationProgress:
    """Whether the migration now running has committed any of its own work.

    Alembic commits each migration on its own (``transaction_per_migration``),
    right after ``on_version_apply`` stamps its revision. Any other commit is an
    ``autocommit_block`` committing part of the migration that is still
    running; that migration can no longer be undone by a rollback, so it is not
    run again.
    """

    def __init__(self, connection: Connection) -> None:
        self.committed_partway = False
        self._stamped = False
        event.listen(connection, "commit", self._committed)

    def version_applied(self, **_: Any) -> None:
        """Alembic's ``on_version_apply`` callback."""
        self._stamped = True

    def _committed(self, _connection: Connection) -> None:
        if self._stamped:
            self._stamped = False
            self.committed_partway = False
        else:
            self.committed_partway = True


def _given_up_by_a_retry_loop(exc: DBAPIError) -> bool:
    """Whether this lock error escaped a PL/pgSQL block: a ``with_lock_retries``
    loop (``migration_helpers``) catches every lock error inside it and lets one
    out only once it has spent its own attempts, re-raised from inside the
    ``DO`` block, which Postgres records in the error's CONTEXT."""
    context = getattr(getattr(exc.orig, "__cause__", None), "context", None) or ""
    return "PL/pgSQL function" in context


#: Retries start only within this long of the first attempt, so the upgrade
#: fails inside the deploy job's budget (deploy-prod.yml: 30 minutes); the last
#: attempt can still run a ``with_lock_retries`` loop of about 320 s after it.
MIGRATION_RETRY_BUDGET_S = 600.0


def run_migrations_retrying_lock_contention(
    connection: Connection,
    progress: MigrationProgress,
    run: Callable[[], None],
    *,
    attempts: int = 10,
    budget_s: float = MIGRATION_RETRY_BUDGET_S,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> None:
    """Run ``run`` (the upgrade), and when a migration loses a lock race with
    the backend still serving, roll that migration back and run it again.

    A deploy migrates while the previous release serves (``.claude/rules/
    migrations.md``), so a migration's DDL competes for locks with live
    requests. ``lock_timeout`` keeps it from stalling them; this makes losing
    that race cost a retry instead of the deploy. Every lock a migration takes
    is covered, the ones it takes implicitly included (dropping a column drops
    its foreign key, which locks the referenced table): the transaction that
    timed out or was chosen as a deadlock's victim is rolled back whole, so it
    releases everything and the migration starts over from its first statement.
    This is strong_migrations' ``lock_timeout_retries`` and GitLab's
    ``with_lock_retries`` for transactional migrations.

    There is one retry layer per lock. A ``with_lock_retries`` loop already
    retries its own ``LOCK TABLE`` for about 320 s; when it gives up, the tables
    have stayed busy that long and the migration fails rather than running that
    loop again. Retries stop being started ``budget_s`` after the first attempt.

    Migrations already committed stay committed: alembic resumes from the
    stamped revision. A migration that committed part of itself (an
    ``autocommit_block``) is not retried, since running it again would repeat
    work a rollback cannot take back.
    """
    started = clock()
    for attempt in range(1, attempts + 1):
        try:
            run()
            return
        except DBAPIError as exc:
            contention = getattr(exc.orig, "sqlstate", None) in _LOCK_CONTENTION
            wait = min(2.0 ** (attempt - 1), 30.0)
            if (
                not contention
                or _given_up_by_a_retry_loop(exc)
                or progress.committed_partway
                or attempt == attempts
                or clock() + wait - started > budget_s
            ):
                raise
            connection.rollback()
            logger.warning(
                "migration lost a lock to live traffic (%s); rolled back, "
                "retrying in %.0fs (attempt %d of %d)",
                exc.orig,
                wait,
                attempt + 1,
                attempts,
            )
            sleep(wait)


def _set_request_lock_timeout(
    session: Session, transaction: SessionTransaction, connection: Connection
) -> None:
    if transaction.nested:
        return
    # Transaction-local, including transactions opened after an explicit commit
    # or release_read_session(). Background sessions and migrations opt out.
    # PostgreSQL duration syntax, e.g. DB_REQUEST_LOCK_TIMEOUT=5s. The default
    # must expire before work-lease's 10-second answer budget.
    connection.execute(
        text("SELECT set_config('lock_timeout', :value, true)"),
        {"value": os.getenv("DB_REQUEST_LOCK_TIMEOUT", "5s")},
    )


async def get_db() -> AsyncGenerator[AsyncSession]:
    """FastAPI dependency yielding a transactional session."""
    async with async_session_factory() as session:
        event.listen(session.sync_session, "after_begin", _set_request_lock_timeout)
        try:
            yield session
            await session.commit()
        except Exception as exc:
            await session.rollback()
            if (
                isinstance(exc, DBAPIError)
                and getattr(exc.orig, "sqlstate", None) == "55P03"
            ):
                raise HTTPException(
                    status_code=503,
                    detail=say("systemBusy"),
                    headers={"Retry-After": "1"},
                ) from exc
            raise


async def release_read_session(session: AsyncSession) -> None:
    """Release a read-only request's connection before waiting on remote I/O.

    Callers opt in only for read transactions. Closing detaches loaded rows,
    whose scalar values remain available, and permits subsequent reads.
    """
    if session.new or session.dirty or session.deleted:
        raise RuntimeError("Remote read attempted with pending database writes")
    await session.close()


def after_own_commit(
    session: AsyncSession, run: Callable[[], None], *, once: bool = True
) -> None:
    """Run ``run`` once ``session``'s own transaction commits — and not before.

    ``after_commit`` fires for a released ``begin_nested()`` savepoint too: SQLAlchemy
    dispatches it from ``SessionTransaction.commit`` whenever the transaction being
    committed has no parent *or* is a savepoint. Releasing a savepoint commits nothing
    — another connection still reads the rows as they were — so work that opens its own
    session to look at what this commit wrote would read the state from before it.
    A request does release savepoints: ``review.task_landing.tell_origin`` wraps the
    line it says in one so that a courtesy line cannot take a landing back with it.
    An event fired while a savepoint is still open is therefore left to the commit
    that ends ``session``'s transaction.

    ``once`` runs ``run`` for that one commit, the way ``event.listen(..., once=True)``
    would; otherwise a session that commits more than once runs it each time.
    """
    done = False

    def committed(_session: Session) -> None:
        nonlocal done
        if _session.in_nested_transaction():
            return
        if once:
            if done:
                return
            done = True
        run()

    event.listen(session.sync_session, "after_commit", committed)
