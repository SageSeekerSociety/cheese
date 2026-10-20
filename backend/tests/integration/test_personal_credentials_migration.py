"""P3's unavailable shared assistants recover through references, with no secret copies."""

import uuid

from tests.integration.migration_replay import database_at, seed_room


def test_shared_assistants_recover_the_original_personal_credentials():
    with database_at("e7a4913c62bf") as db:
        project, _ = seed_room(db)
        shared = db.fetchval("SELECT team_id FROM projects WHERE id = $1", project)
        owner = db.fetchval(
            'INSERT INTO "user" (username, email, created_at, updated_at) VALUES ($1, $2, now(), now()) RETURNING id',
            "p6-" + uuid.uuid4().hex[:8],
            uuid.uuid4().hex + "@example.test",
        )
        home = db.fetchval(
            "INSERT INTO team (id, name, intro, description, avatar_id, personal_owner_user_id, created_at, updated_at) VALUES (nextval('team_seq'), 'personal', '', '', 0, $1, now(), now()) RETURNING id",
            owner,
        )
        db.execute(
            "INSERT INTO team_user_relation (id, user_id, team_id, role, created_at, updated_at) VALUES (nextval('team_user_relation_seq'), $1, $2, 2, now(), now())",
            owner,
            shared,
        )
        key, login = uuid.uuid4(), uuid.uuid4().hex
        db.execute(
            "INSERT INTO account_model_connections (id, team_id, kind, key, key_last4, allowed_models, created_at, updated_at) VALUES ($1, $2, 'api_key', 'sealed-original', '1234', '[\"sonnet\"]'::jsonb, now(), now())",
            key,
            home,
        )
        db.execute(
            "INSERT INTO device (device_id, name, token, team_id, owner_user_id, created_at) VALUES ($1, 'private computer', $2, $3, $4, now())",
            login,
            uuid.uuid4().hex,
            home,
            owner,
        )
        db.execute(
            "INSERT INTO claude_logins (id, device_id, local_id, installed, logged_in, auth_method, subscription_type, allowed_models, checked_at) VALUES ($1, $1, 'default', true, true, 'claude.ai', 'max', '[\"sonnet\"]'::jsonb, now())",
            login,
        )
        instances = []
        for field, credential in [("account_connection_id", key), ("login_id", login)]:
            instance = uuid.uuid4()
            instances.append(instance)
            db.execute(
                "INSERT INTO agent_instances (id, project_id, handle, configuration, display_name, is_active, created_at, updated_at) VALUES ($1, $2, $3, '{\"model\":\"sonnet\"}'::json, 'personal assistant', true, now(), now())",
                instance,
                project,
                "own-" + uuid.uuid4().hex[:8],
            )
            db.execute(
                f"INSERT INTO own_agents (instance_id, owner_user_id, harness, {field}) VALUES ($1, $2, 'claude_code', $3)",
                instance,
                owner,
                credential,
            )
        db.upgrade("f4b1298ca631")
        assert (
            db.fetchval("SELECT key FROM account_model_connections WHERE id = $1", key)
            == "sealed-original"
        )
        assert (
            db.fetchval(
                "SELECT owner_user_id FROM account_model_connections WHERE id = $1", key
            )
            == owner
        )
        assert (
            db.fetchval("SELECT team_id FROM claude_logins WHERE id = $1", login)
            == home
        )
        assert (
            db.fetchval("SELECT owner_user_id FROM claude_logins WHERE id = $1", login)
            == owner
        )
        refs = db.fetch(
            "SELECT login_id, account_connection_id FROM personal_model_resources WHERE team_id = $1",
            shared,
        )
        assert {(r["login_id"], r["account_connection_id"]) for r in refs} == {
            (login, None),
            (None, key),
        }
        assert (
            db.fetchval(
                "SELECT count(*) FROM account_model_connections WHERE key = 'sealed-original'"
            )
            == 1
        )
        assert (
            db.fetchval(
                "SELECT count(*) FROM agent_instances WHERE id = ANY($1::uuid[]) AND is_active",
                instances,
            )
            == 2
        )
        # The old serving backend can still insert while the first release rolls.
        other = uuid.uuid4().hex
        db.execute(
            "INSERT INTO claude_logins (id, device_id, local_id, installed, logged_in, checked_at) VALUES ($1, $2, 'other', true, true, now())",
            other,
            login,
        )
        assert (
            db.fetchval("SELECT team_id FROM claude_logins WHERE id = $1", other)
            == home
        )
