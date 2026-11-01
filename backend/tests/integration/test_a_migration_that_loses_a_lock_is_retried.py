"""A deploy's migration that loses a lock to live traffic runs again instead of
failing the deploy.

A deploy migrates while the previous release is still serving, and every
migration runs under a short ``lock_timeout`` so it never stalls those requests
for long. Before this, losing that race — a timeout, or being the deadlock's
victim — failed the deploy (dev deploys of ``41a261d02e9e``, ``b6fcc6362b79``,
``b8d3f1a6c254``). Now the migration's transaction is rolled back whole and the
migration starts over once the request has finished.

Runs the real ``alembic upgrade`` on a scratch database one revision behind a
migration that alters a table the backend reads on every turn.
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

#: Adds ``agent_turns.stop_reason`` without naming the table up front, so its
#: ``ALTER TABLE`` waits in line behind whoever is reading ``agent_turns``.
_MIGRATION = "a3c9e15f7b20"
_BEFORE = "e73ca7b0dd69"
_BACKEND = Path(__file__).resolve().parents[2]


def _hold_a_read(
    dsn: str,
    table: str,
    held: threading.Event,
    *,
    seconds: float | None = None,
    past_a_waiter_by: float | None = None,
) -> None:
    """A live request reading ``table`` in an open transaction. It ends after
    ``seconds``, or ``past_a_waiter_by`` seconds after someone starts waiting
    for a lock on ``table`` behind it."""

    async def go() -> None:
        conn = await asyncpg.connect(dsn)
        watcher = await asyncpg.connect(dsn)
        try:
            async with conn.transaction():
                await conn.execute(f"SELECT 1 FROM {table} LIMIT 1")
                held.set()
                if seconds is not None:
                    await asyncio.sleep(seconds)
                    return
                for _ in range(600):
                    if await watcher.fetchval(
                        "SELECT count(*) FROM pg_locks l JOIN pg_class c"
                        " ON c.oid = l.relation"
                        " WHERE c.relname = $1 AND NOT l.granted",
                        table,
                    ):
                        break
                    await asyncio.sleep(0.05)
                await asyncio.sleep(past_a_waiter_by)
        finally:
            await watcher.close()
            await conn.close()

    asyncio.run(go())


def test_a_migration_that_times_out_behind_a_live_request_completes_after_it():
    with database_at(_BEFORE) as db:
        held = threading.Event()
        request = threading.Thread(
            target=_hold_a_read,
            args=(db.url.replace("+asyncpg", ""), "agent_turns", held),
            # Longer than the migration's lock_timeout, so its first try loses.
            kwargs={"past_a_waiter_by": 1.5},
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
        assert db.fetchval(
            "SELECT count(*) FROM information_schema.columns"
            " WHERE table_name = 'agent_turns' AND column_name = 'stop_reason'"
        )


def test_a_migration_that_committed_part_of_itself_is_not_run_again():
    """Work an ``autocommit_block`` committed cannot be rolled back, so the
    migration that did it is not repeated; the deploy fails as before."""
    from app.core.db import (
        MigrationProgress,
        run_migrations_retrying_lock_contention,
    )

    with database_at(_BEFORE) as db:
        held = threading.Event()
        request = threading.Thread(
            target=_hold_a_read,
            args=(db.url.replace("+asyncpg", ""), "agent_turns", held),
            kwargs={"seconds": 2.0},
        )
        request.start()
        runs = 0

        def migrate(connection) -> None:
            progress = MigrationProgress(connection)

            def upgrade() -> None:
                nonlocal runs
                runs += 1
                connection.execute(text("CREATE TABLE IF NOT EXISTS partly (n int)"))
                connection.commit()  # what an autocommit_block does
                connection.execute(text("SET lock_timeout = '200ms'"))
                connection.execute(text("ALTER TABLE agent_turns ADD COLUMN x int"))

            run_migrations_retrying_lock_contention(
                connection, progress, upgrade, sleep=lambda _s: None
            )

        async def go() -> None:
            engine = create_async_engine(db.url)
            try:
                async with engine.connect() as conn:
                    await conn.run_sync(migrate)
            finally:
                await engine.dispose()

        try:
            assert held.wait(10)
            with pytest.raises(DBAPIError, match="lock timeout"):
                asyncio.run(go())
        finally:
            request.join()
        assert runs == 1
