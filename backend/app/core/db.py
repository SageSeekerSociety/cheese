"""Async SQLAlchemy engine, session factory, and declarative base."""

import json
import os
from collections.abc import AsyncGenerator
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.core.config import settings

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

# Under pytest one worker process runs MANY event loops (anyio function scope,
# blocking portals), and a pooled asyncpg connection must never hop loops — so
# the test conftest forces NullPool (fresh connection per checkout) via this
# env var BEFORE any app import. Production keeps the default QueuePool: one
# process, one loop, one pool.
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

__all__ = [
    "AsyncSession",
    "Base",
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
