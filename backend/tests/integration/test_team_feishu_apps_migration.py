"""4288e24987e2 → 499940a660b1: team Feishu apps arrive without touching a
single existing connection, so the release still serving during the deploy — and
one rolled back to — reads every row exactly as it wrote it. The steps are split
so a run cut short can be run again, and the foreign key to ``team`` never
scans or blocks team writes under a lock."""

import json
import uuid

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
