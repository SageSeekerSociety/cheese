"""Interrupted index builds resume, and old rollout snapshots do not abort them."""

import asyncio
import os
from pathlib import Path
import sys
import uuid

import asyncpg

from tests.integration.migration_replay import database_at, seed_room

BACKEND = Path(__file__).resolve().parents[2]
REVISION = "f4b1298ca631"


def _seed(db):
    project, _ = seed_room(db)
    team = db.fetchval("SELECT team_id FROM projects WHERE id = $1", project)
    login, key = uuid.uuid4().hex, uuid.uuid4()
    db.execute(
        "INSERT INTO device (device_id, name, token, team_id, created_at) VALUES ($1, 'computer', $2, $3, now())",
        login,
        uuid.uuid4().hex,
        team,
    )
    db.execute(
        "INSERT INTO claude_logins (id, device_id, local_id, installed, logged_in, auth_method, checked_at) VALUES ($1, $1, 'default', true, true, 'claude.ai', now())",
        login,
    )
    db.execute(
        "INSERT INTO account_model_connections (id, team_id, kind, key, key_last4, allowed_models, created_at, updated_at) VALUES ($1, $2, 'api_key', 'sealed-original', '1234', '[\"sonnet\"]'::jsonb, now(), now())",
        key,
        team,
    )
    return team, login, key


async def _start(db):
    return await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "alembic",
        "upgrade",
        REVISION,
        cwd=BACKEND,
        env={**os.environ, "DATABASE_URL": db.url, "MIGRATION_LOCK_TIMEOUT": "100ms"},
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )


async def _waiting_for_snapshot(db, process):
    monitor = await asyncpg.connect(db.url.replace("+asyncpg", ""))
    try:
        async with asyncio.timeout(10):
            while True:
                index = await monitor.fetchrow(
                    "SELECT c.relname, p.pid FROM pg_stat_progress_create_index p JOIN pg_class c ON c.oid=p.index_relid WHERE p.datname=current_database() AND p.phase='waiting for old snapshots'"
                )
                if index:
                    return index["relname"], index["pid"]
                if process.returncode is not None:
                    _, errors = await process.communicate()
                    raise AssertionError(errors.decode())
                await asyncio.sleep(0.01)
    finally:
        await monitor.close()


async def _reap(process):
    if process.returncode is None:
        process.kill()
        await process.communicate()


def _preserved(db, team, login, key):
    assert db.fetchval("SELECT team_id FROM claude_logins WHERE id = $1", login) == team
    assert (
        db.fetchval("SELECT host_id FROM claude_logins WHERE id = $1", login) == login
    )
    assert (
        db.fetchval("SELECT key FROM account_model_connections WHERE id = $1", key)
        == "sealed-original"
    )
    assert (
        db.fetchval(
            "SELECT count(*) FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid WHERE i.indisvalid AND c.relname = ANY($1::text[])",
            [
                "ix_claude_logins_team_id",
                "ix_claude_logins_owner_user_id",
                "ix_claude_logins_host_id",
                "ix_account_model_connections_owner_user_id",
            ],
        )
        == 4
    )


def test_interrupted_credential_migration_preserves_credentials_when_retried():
    with database_at("e7a4913c62bf") as db:
        ids = _seed(db)

        async def interrupt(conn):
            async with conn.transaction(isolation="repeatable_read", readonly=True):
                await conn.fetchval("SELECT count(*) FROM topics")
                process = await _start(db)
                try:
                    index, pid = await _waiting_for_snapshot(db, process)
                    assert await conn.fetchval("SELECT pg_cancel_backend($1)", pid)
                    await asyncio.wait_for(process.communicate(), 5)
                    assert process.returncode != 0
                    return index
                finally:
                    await _reap(process)

        interrupted_index = db.run(interrupt)
        assert db.fetchval("SELECT version_num FROM alembic_version") == "e7a4913c62bf"
        assert db.fetchval("SELECT to_regclass('model_credential_hosts')") is not None
        assert (
            db.fetchval(
                "SELECT indisvalid FROM pg_index WHERE indexrelid=$1::regclass",
                interrupted_index,
            )
            is False
        )
        valid = dict(
            db.fetch(
                "SELECT c.relname, c.oid FROM pg_class c JOIN pg_index i ON i.indexrelid=c.oid WHERE i.indisvalid AND c.relname LIKE 'ix_claude_logins_%'"
            )
        )
        db.upgrade(REVISION)
        for name, oid in valid.items():
            assert db.fetchval("SELECT $1::regclass::oid", name) == oid
        _preserved(db, *ids)


def test_old_backend_snapshot_can_outlast_lock_timeout_without_aborting_rollout():
    with database_at("e7a4913c62bf") as db:
        ids = _seed(db)

        async def release_after_timeout(conn):
            process = None
            try:
                async with conn.transaction(isolation="repeatable_read", readonly=True):
                    await conn.fetchval("SELECT count(*) FROM topics")
                    process = await _start(db)
                    await _waiting_for_snapshot(db, process)
                    # Outlast the configured lock timeout, then let the old request end.
                    await asyncio.sleep(0.35)
                _, errors = await asyncio.wait_for(process.communicate(), 10)
                assert process.returncode == 0, errors.decode()
            finally:
                if process is not None:
                    await _reap(process)

        db.run(release_after_timeout)
        assert db.fetchval("SELECT version_num FROM alembic_version") == REVISION
        _preserved(db, *ids)
