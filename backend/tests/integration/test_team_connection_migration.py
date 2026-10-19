"""Credentials survive team backfill and writes from the serving old backend."""

import uuid

from tests.integration.migration_replay import database_at


def test_existing_connection_gets_personal_team_and_old_backend_can_still_insert():
    with database_at("d1f37c8a46b2") as db:
        user = db.fetchval(
            'INSERT INTO "user" (username, email, created_at, updated_at) VALUES ($1, $2, now(), now()) RETURNING id',
            "model-migration-" + uuid.uuid4().hex[:8],
            uuid.uuid4().hex + "@example.test",
        )
        connection = uuid.uuid4()
        db.execute(
            "INSERT INTO account_model_connections (id, owner_user_id, kind, key, key_last4, model, created_at, updated_at) VALUES ($1, $2, 'model_service', 'sealed-original', '1234', 'kimi-test', now(), now())",
            connection,
            user,
        )
        db.upgrade("d4f30c9e71a2")
        row = db.fetchrow(
            "SELECT c.*, t.personal_owner_user_id FROM account_model_connections c JOIN team t ON t.id = c.team_id WHERE c.id = $1",
            connection,
        )
        assert row["key"] == "sealed-original"
        assert row["personal_owner_user_id"] == user
        assert row["allowed_models"] == '["kimi-test"]'
        old_insert = uuid.uuid4()
        db.execute(
            "INSERT INTO account_model_connections (id, owner_user_id, kind, key, key_last4, created_at, updated_at) VALUES ($1, $2, 'api_key', 'sealed-second', '5678', now(), now())",
            old_insert,
            user,
        )
        assert (
            db.fetchval(
                "SELECT team_id FROM account_model_connections WHERE id = $1",
                old_insert,
            )
            == row["team_id"]
        )
        assert (
            db.fetchval(
                "SELECT allowed_models FROM account_model_connections WHERE id = $1",
                old_insert,
            )
            == '["opus", "sonnet", "haiku"]'
        )
        # The previous mapping can select and update every field it knew.
        db.execute(
            "UPDATE account_model_connections SET name = 'old backend' WHERE id = $1",
            connection,
        )
        assert (
            db.fetchval(
                "SELECT key FROM account_model_connections WHERE id = $1", connection
            )
            == "sealed-original"
        )
