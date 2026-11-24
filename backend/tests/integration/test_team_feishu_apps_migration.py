"""4288e24987e2: a Feishu connection that carried its own app moves the app to its
owner's personal team, keeps the owner's authorization, and keeps working."""

import json
import uuid
from datetime import UTC, datetime

from app.core.crypto import Purpose, decrypt, encrypt
from tests.integration.migration_replay import database_at

BEFORE = "fbdb95e20845"
AFTER = "4288e24987e2"


def _connection(db, owner: int, app_id: str, secret: dict, updated: str) -> str:
    row_id = uuid.uuid4()
    sealed = encrypt(
        Purpose.INTEGRATION_SECRET, json.dumps(secret), bound_to=str(row_id)
    )
    db.execute(
        "INSERT INTO integrations (id, owner_user_id, owner_handle, provider, label,"
        " config, secret, grants, status, last_error, created_at, updated_at)"
        " VALUES ($1, $2, $3, 'feishu', '飞书', $4::jsonb, $5, '[]'::jsonb, 'ok', '',"
        " now(), $6)",
        row_id,
        owner,
        f"u{owner}",
        json.dumps({"app_id": app_id, "domain": "feishu", "folders": []}),
        sealed,
        datetime.fromisoformat(updated).replace(tzinfo=UTC),
    )
    return str(row_id)


def _opened(row_id: str, stored: str) -> dict:
    return json.loads(decrypt(Purpose.INTEGRATION_SECRET, stored, bound_to=row_id))


def test_own_app_connections_move_to_their_owners_personal_team():
    with database_at(BEFORE) as db:
        tokens = {"user_access_token": "u", "refresh_token": "r"}
        # An owner with no personal team yet, and one with two connections to
        # two different apps.
        alone = _connection(
            db, 9101, "cli_a", {"app_secret": "sa", **tokens}, "2026-09-27"
        )
        older = _connection(
            db, 9102, "cli_old", {"app_secret": "so", **tokens}, "2026-09-01"
        )
        newer = _connection(
            db, 9102, "cli_new", {"app_secret": "sn", **tokens}, "2026-10-01"
        )

        db.upgrade(AFTER)

        apps = {
            r["owner"]: r
            for r in db.fetch(
                "SELECT t.personal_owner_user_id AS owner, a.team_id, a.app_id,"
                " a.secret FROM team_feishu_apps a JOIN team t ON t.id = a.team_id"
            )
        }
        assert set(apps) == {9101, 9102}
        assert apps[9101]["app_id"] == "cli_a"
        assert apps[9102]["app_id"] == "cli_new", "the newest connection's app wins"
        team_secret = json.loads(
            decrypt(
                Purpose.INTEGRATION_SECRET,
                apps[9101]["secret"],
                bound_to=f"team-feishu-app:{apps[9101]['team_id']}",
            )
        )
        assert team_secret == {"app_secret": "sa"}
        # The owner is on the personal team the migration made.
        assert (
            db.fetchval(
                "SELECT role FROM team_user_relation WHERE team_id = $1 AND user_id = 9101",
                apps[9101]["team_id"],
            )
            == 0
        )

        rows = {
            str(r["id"]): r
            for r in db.fetch("SELECT id, config, secret FROM integrations")
        }
        for row_id, owner in ((alone, 9101), (older, 9102), (newer, 9102)):
            config = json.loads(rows[row_id]["config"])
            assert config == {"folders": [], "team_id": apps[owner]["team_id"]}
            assert _opened(row_id, rows[row_id]["secret"]) == tokens
