"""4288e24987e2 → 499940a660b1: team Feishu apps arrive without touching a
single existing connection, so the release still serving during the deploy — and
one rolled back to — reads every row exactly as it wrote it. The steps are split
so a run cut short can be run again, and the foreign key to ``team`` never
scans or blocks team writes under a lock."""

import asyncio
import json
import threading
import time
import uuid

import asyncpg

from tests.integration.migration_replay import database_at

BEFORE = "fbdb95e20845"
AFTER = "499940a660b1"
FK = "fk_team_feishu_apps_team_id_team"


def _connection(db, owner: int, config: dict, secret: str) -> str:
    row_id = uuid.uuid4()
    db.execute(
        "INSERT INTO integrations (id, owner_user_id, owner_handle, provider, label,"
        " config, secret, grants, status, last_error, created_at, updated_at)"
        " VALUES ($1, $2, $3, 'feishu', '飞书', $4::jsonb, $5, '[]'::jsonb, 'ok', '',"
        " now(), now())",
        row_id,
        owner,
        f"u{owner}",
        json.dumps(config),
        secret,
    )
    return str(row_id)


def _rows(db) -> dict:
    return {
        str(r["id"]): (json.loads(r["config"]), r["secret"], r["status"])
        for r in db.fetch("SELECT id, config, secret, status FROM integrations")
    }


def test_existing_connections_are_left_exactly_as_they_were():
    with database_at(BEFORE) as db:
        _connection(db, 9101, {"app_id": "cli_a", "domain": "feishu"}, "v1:k:sealed-a")
        _connection(db, 9102, {}, "v1:k:sealed-platform")
        before = _rows(db)

        db.upgrade(AFTER)

        assert _rows(db) == before
        assert db.fetchval("SELECT count(*) FROM team_feishu_apps") == 0

        db.downgrade(BEFORE)
        assert _rows(db) == before
        assert db.fetchval("SELECT to_regclass('team_feishu_apps')") is None


def test_a_run_cut_short_after_the_table_is_made_runs_again():
    """The table already there (42P07 otherwise), the foreign key already added
    unvalidated: running the steps again finishes them."""
    with database_at(BEFORE) as db:
        db.upgrade("6bfa39e854c4")
        db.execute("DELETE FROM alembic_version")
        db.execute(f"INSERT INTO alembic_version VALUES ('{BEFORE}')")

        db.upgrade(AFTER)

        assert db.fetchval(
            f"SELECT convalidated FROM pg_constraint WHERE conname = '{FK}'"
        )
        assert (
            db.fetchval("SELECT to_regclass('uq_integrations_owner_feishu_team')")
            is not None
        )


def test_a_same_named_constraint_on_another_table_does_not_stand_in_for_the_key():
    """The foreign key is recognised as there by its name on
    ``team_feishu_apps``, not by its name anywhere."""
    with database_at(BEFORE) as db:
        db.upgrade("4288e24987e2")
        db.execute("CREATE TABLE feishu_decoy (id integer)")
        db.execute(f"ALTER TABLE feishu_decoy ADD CONSTRAINT {FK} CHECK (id > 0)")

        db.upgrade(AFTER)

        assert db.fetchval(
            "SELECT convalidated FROM pg_constraint WHERE conname = $1"
            " AND conrelid = 'team_feishu_apps'::regclass AND contype = 'f'",
            FK,
        )


def _hold(dsn: str, sql: str, held: threading.Event, seconds: float) -> None:
    async def go() -> None:
        conn = await asyncpg.connect(dsn)
        try:
            async with conn.transaction():
                await conn.execute(sql)
                held.set()
                await asyncio.sleep(seconds)
        finally:
            await conn.close()

    asyncio.run(go())


def test_validating_the_key_behind_a_live_lock_waits_its_turn():
    """Validation that meets a lock held longer than the migration's lock
    timeout lets go and tries again, and finishes once the lock is gone."""
    with database_at(BEFORE) as db:
        db.upgrade("6bfa39e854c4")
        held = threading.Event()
        holder = threading.Thread(
            target=_hold,
            args=(
                db.url.replace("+asyncpg", ""),
                "LOCK TABLE team_feishu_apps IN SHARE UPDATE EXCLUSIVE MODE",
                held,
                4.0,
            ),
        )
        holder.start()
        assert held.wait(10)
        started = time.monotonic()
        try:
            db.upgrade("8a9d4083c7c7", {"MIGRATION_LOCK_TIMEOUT": "300ms"})
        finally:
            holder.join()
        assert time.monotonic() - started > 1
        assert db.fetchval(
            "SELECT convalidated FROM pg_constraint WHERE conname = $1"
            " AND conrelid = 'team_feishu_apps'::regclass",
            FK,
        )
