"""4288e24987e2: adds team Feishu apps without touching a single existing
connection, so the release still serving during the deploy — and one rolled
back to — reads every row exactly as it wrote it."""

import json
import uuid

from tests.integration.migration_replay import database_at

BEFORE = "fbdb95e20845"
AFTER = "4288e24987e2"


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
