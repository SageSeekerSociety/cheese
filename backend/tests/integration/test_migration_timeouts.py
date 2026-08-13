"""The migration-timeout guard is live on the real connection (#356).

The unit test pins that ``apply_migration_timeouts`` issues the right SET; this
one proves PostgreSQL actually honours it — ``SHOW lock_timeout`` on the very
connection a migration would run on returns the configured value. That is the
whole mechanism #356 needs: a migration that can't grab its lock fails fast
instead of blocking the deploy for 30 minutes.
"""

import pytest
from sqlalchemy import text

from app.core.config import settings
from app.core.db import apply_migration_timeouts, engine


@pytest.mark.anyio
async def test_show_returns_the_configured_timeouts(_pg_schema, monkeypatch) -> None:
    monkeypatch.setattr(settings, "migration_lock_timeout", "7s")
    monkeypatch.setattr(settings, "migration_statement_timeout", "250ms")

    def _apply_and_read(sync_conn) -> tuple[str, str]:
        # Mirror do_run_migrations: apply the guard inside the transaction the
        # migrations run in, then read it back on the SAME connection.
        with sync_conn.begin():
            apply_migration_timeouts(sync_conn)
            lock = sync_conn.execute(text("SHOW lock_timeout")).scalar_one()
            stmt = sync_conn.execute(text("SHOW statement_timeout")).scalar_one()
        return lock, stmt

    async with engine.connect() as conn:
        lock, stmt = await conn.run_sync(_apply_and_read)

    assert lock == "7s"
    assert stmt == "250ms"


@pytest.mark.anyio
async def test_default_lock_timeout_is_bounded(_pg_schema) -> None:
    """With shipped defaults the lock wait is a few seconds, not unbounded — the
    difference between a fast-failing deploy and the #356 30-minute brownout."""

    def _apply_and_read(sync_conn) -> str:
        with sync_conn.begin():
            apply_migration_timeouts(sync_conn)
            return sync_conn.execute(text("SHOW lock_timeout")).scalar_one()

    async with engine.connect() as conn:
        lock = await conn.run_sync(_apply_and_read)

    # PostgreSQL normalises "10s" → "10s"; the point is it is a small, finite bound.
    assert lock == "10s"
