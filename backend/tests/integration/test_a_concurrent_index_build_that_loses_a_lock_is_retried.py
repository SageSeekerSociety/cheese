"""A ``CREATE INDEX CONCURRENTLY`` that times out behind live traffic is built
again, and the deploy ends with a valid index.

A concurrent build waits for every transaction writing its table to end, under
the migration's ``lock_timeout``. It runs in an ``autocommit_block``, so the
migration around it has already committed part of itself and cannot be rolled
back and rerun; before this, a build that timed out failed the deploy (dev
deploys of ``5d0e8b3c7a21`` on 2026-10-16 and ``f4b1298ca631`` on 2026-10-20)
and left an INVALID index behind, which a rerun with ``IF NOT EXISTS`` would
have taken for the finished one.
"""

import asyncio
import os
import subprocess
import sys
import threading
from pathlib import Path

import asyncpg
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from tests.integration.migration_replay import database_at

#: Builds ``ix_blocks_queued_messages`` on ``blocks`` CONCURRENTLY, IF NOT EXISTS.
_MIGRATION = "c7e2a91f4d3b"
_BEFORE = "7d2e9a41c6b3"
_INDEX = "ix_blocks_queued_messages"
_BACKEND = Path(__file__).resolve().parents[2]

_BUILD_WAITING = (
    "SELECT count(*) FROM pg_stat_activity"
    " WHERE datname = current_database() AND wait_event_type = 'Lock'"
    " AND query ILIKE '%INDEX CONCURRENTLY%'"
)


def _hold_a_write(
    dsn: str,
    held: threading.Event,
    *,
    seconds: float | None = None,
    past_a_build_waiting_by: float | None = None,
) -> None:
    """A live request with an uncommitted write to ``blocks``. It ends after
    ``seconds``, or ``past_a_build_waiting_by`` seconds after a concurrent
    index build starts waiting for it."""

    async def go() -> None:
        conn = await asyncpg.connect(dsn)
        watcher = await asyncpg.connect(dsn)
        try:
            async with conn.transaction():
                await conn.execute("UPDATE blocks SET kind = kind WHERE false")
                held.set()
                if seconds is not None:
                    await asyncio.sleep(seconds)
                    return
                for _ in range(2400):  # the migration's process may start slowly
                    if await watcher.fetchval(_BUILD_WAITING):
                        break
                    await asyncio.sleep(0.05)
                await asyncio.sleep(past_a_build_waiting_by)
        finally:
            await watcher.close()
            await conn.close()

    asyncio.run(go())


def _index_valid(db) -> bool | None:
    return db.fetchval(
        "SELECT indisvalid FROM pg_index WHERE indexrelid = to_regclass($1)", _INDEX
    )


def test_a_concurrent_build_that_times_out_behind_a_live_write_ends_with_a_valid_index():
    with database_at(_BEFORE) as db:
        held = threading.Event()
        request = threading.Thread(
            target=_hold_a_write,
            args=(db.url.replace("+asyncpg", ""), held),
            # Longer than the build's lock_timeout, so its first try loses.
            kwargs={"past_a_build_waiting_by": 1.5},
        )
        request.start()
        try:
            assert held.wait(10)
            result = subprocess.run(
                [sys.executable, "-m", "alembic", "upgrade", _MIGRATION],
                cwd=_BACKEND,
                env={
                    **os.environ,
                    "DATABASE_URL": db.url,
                    "MIGRATION_LOCK_TIMEOUT": "300ms",
                },
                capture_output=True,
                text=True,
            )
        finally:
            request.join()

        assert result.returncode == 0, result.stderr
        assert "lost a lock to live traffic" in result.stderr  # the first try lost
        assert db.fetchval("SELECT version_num FROM alembic_version") == _MIGRATION
        assert _index_valid(db) is True


def test_a_build_that_keeps_losing_fails_and_a_later_run_replaces_its_invalid_index():
    """Once the upgrade's retries are spent the build fails loudly, leaving the
    INVALID index a failed concurrent build always leaves; the next deploy's
    run drops it and builds a valid one instead of accepting it."""
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    import app.core.migration_impl  # noqa: F401  (what alembic/env.py imports)
    from app.core.db import LockRaceRetries

    def build(retries: LockRaceRetries) -> None:
        def go_sync(connection) -> None:
            connection.execute(text("SET lock_timeout = '200ms'"))
            connection.commit()
            context = MigrationContext.configure(
                connection, opts={"lock_race_retries": retries}
            )
            with context.autocommit_block():
                Operations(context).create_index(
                    _INDEX,
                    "blocks",
                    ["created_at", "id"],
                    postgresql_concurrently=True,
                    if_not_exists=True,
                )

        async def go() -> None:
            engine = create_async_engine(db.url)
            try:
                async with engine.connect() as conn:
                    await conn.run_sync(go_sync)
            finally:
                await engine.dispose()

        asyncio.run(go())

    with database_at(_BEFORE) as db:
        held = threading.Event()
        request = threading.Thread(
            target=_hold_a_write,
            args=(db.url.replace("+asyncpg", ""), held),
            kwargs={"seconds": 3.0},
        )
        request.start()
        try:
            assert held.wait(10)
            with pytest.raises(DBAPIError, match="lock timeout"):
                build(LockRaceRetries(attempts=3, sleep=lambda _s: None))
        finally:
            request.join()
        assert _index_valid(db) is False

        build(LockRaceRetries(sleep=lambda _s: None))
        assert _index_valid(db) is True
