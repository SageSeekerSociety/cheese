"""The ownership cleanup preserves credentials and project choices."""

import json
import uuid

from tests.integration.migration_replay import database_at, seed_room

OWNERSHIP = "d4f30c9e71a2"
CLEANUP = "e7a4913c62bf"


def test_cleanup_preserves_connections_logins_and_unrelated_project_settings():
    with database_at(OWNERSHIP) as db:
        project, _ = seed_room(db)
        shared_team = db.fetchval("SELECT team_id FROM projects WHERE id = $1", project)
        owner = db.fetchval(
            'INSERT INTO "user" (username, email, created_at, updated_at)'
            " VALUES ($1, $2, now(), now()) RETURNING id",
            "connection-cleanup-" + uuid.uuid4().hex[:8],
            uuid.uuid4().hex + "@example.test",
        )
        db.execute(
            "INSERT INTO account_model_connections"
            " (id, owner_user_id, kind, key, key_last4, model, allowed_models, created_at, updated_at)"
            " VALUES ($1, $2, 'model_service', 'sealed-personal', '1234', 'custom-v1',"
            ' \'["custom-v1","custom-v2"]\'::jsonb, now(), now())',
            uuid.uuid4(),
            owner,
        )
        shared_connection = uuid.uuid4()
        db.execute(
            "INSERT INTO account_model_connections"
            " (id, team_id, kind, key, key_last4, allowed_models, created_at, updated_at)"
            " VALUES ($1, $2, 'api_key', 'sealed-shared', '5678',"
            " '[\"sonnet\"]'::jsonb, now(), now())",
            shared_connection,
            shared_team,
        )
        device = uuid.uuid4().hex
        db.execute(
            "INSERT INTO device (device_id, name, token, team_id, owner_user_id, created_at)"
            " VALUES ($1, 'Workstation', $2, $3, $4, now())",
            device,
            uuid.uuid4().hex,
            shared_team,
            owner,
        )
        db.execute(
            "INSERT INTO claude_logins"
            " (id, device_id, local_id, installed, logged_in, auth_method,"
            " subscription_type, allowed_models, checked_at)"
            " VALUES ($1, $1, 'default', true, true, 'claude.ai', 'max',"
            ' \'["opus","sonnet"]\'::jsonb, now())',
            device,
        )
        retained = {
            "execution_environment": {"kind": "sandbox", "spec": "team_devices"},
            "theme": "dark",
            "custom": {"own_agent_kinds": ["retain-nested-value"]},
        }
        db.execute(
            "UPDATE projects SET settings = $1 WHERE id = $2",
            json.dumps(
                retained
                | {
                    "allow_own_agents": False,
                    "own_agent_kinds": ["api_key"],
                    "own_agent_service_hosts": ["old.example.test"],
                }
            ),
            project,
        )
        connection_query = (
            "SELECT id, team_id, kind, key, key_last4, model, allowed_models"
            " FROM account_model_connections ORDER BY id"
        )
        login_query = "SELECT * FROM claude_logins WHERE id = $1"
        connections_before = [dict(row) for row in db.fetch(connection_query)]
        login_row = db.fetchrow(login_query, device)
        assert login_row is not None
        login_before = dict(login_row)

        db.upgrade(CLEANUP)

        assert [dict(row) for row in db.fetch(connection_query)] == connections_before
        login_after = db.fetchrow(login_query, device)
        assert login_after is not None
        assert dict(login_after) == login_before
        assert (
            json.loads(
                db.fetchval("SELECT settings FROM projects WHERE id = $1", project)
            )
            == retained
        )
        assert not db.fetchval(
            "SELECT EXISTS(SELECT 1 FROM pg_attribute"
            " WHERE attrelid = 'account_model_connections'::regclass"
            " AND attname = 'owner_user_id' AND NOT attisdropped)"
        )
        for catalog, field in (("pg_trigger", "tgname"), ("pg_proc", "proname")):
            assert (
                db.fetchval(
                    f"SELECT count(*) FROM {catalog} WHERE {field} IN"
                    " ('connection_team_during_rollout', 'login_models_during_rollout')"
                )
                == 0
            )
        db.execute(
            "UPDATE account_model_connections SET name = 'Team key' WHERE id = $1",
            shared_connection,
        )
        assert (
            db.fetchval(
                "SELECT key FROM account_model_connections WHERE id = $1",
                shared_connection,
            )
            == "sealed-shared"
        )
