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
from sqlalchemy import event, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool, QueuePool

from app.core.config import settings

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
_engine_kwargs: dict[str, Any] = (
    {"poolclass": NullPool}
    if os.environ.get("CHEESEX_TEST_NULLPOOL")
    else {
        "pool_size": settings.db_pool_size,
        "max_overflow": settings.db_max_overflow,
        "pool_timeout": settings.db_pool_timeout_s,
        "pool_pre_ping": settings.db_pool_pre_ping,
    }
)


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
    ``upgrade head`` uses. Alembic online mode runs the WHOLE upgrade on that
    one connection inside a single transaction (transaction_per_migration is
    off), so setting these once protects every migration in the run — the ones
    present now and any added later.

    ``set_config(name, value, is_local=false)`` is a parameterizable,
    injection-safe equivalent of session-scoped ``SET``, so the guard survives
    even if alembic is later switched to transaction-per-migration.

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


async def get_db() -> AsyncGenerator[AsyncSession]:
    """FastAPI dependency yielding a transactional session."""
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def release_read_session(session: AsyncSession) -> None:
    """Release a read-only request's connection before waiting on remote I/O.

    Callers opt in only for read transactions. Closing detaches loaded rows,
    whose scalar values remain available, and permits subsequent reads.
    """
    if session.new or session.dirty or session.deleted:
        raise RuntimeError("Remote read attempted with pending database writes")
    await session.close()
