"""Credentials survive team backfill and writes from the serving old backend."""

import logging
import uuid
from pathlib import Path
from threading import Thread

from alembic.config import Config
from sqlalchemy import event
from sqlalchemy.engine import Engine

from alembic import command
from app.core.config import settings
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
        assert row is not None
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


def _upgrade_with_old_write(db, monkeypatch, caplog, after_statement, write):
    inserted = []
    errors = []

    def insert():
        try:
            write()
            inserted.append(True)
        except BaseException as exc:
            errors.append(exc)

    def after_execute(conn, cursor, statement, parameters, context, executemany):
        if after_statement in " ".join(statement.split()) and not inserted:
            # The serving backend uses an independent connection while the
            # migration is paused immediately after its backfill.
            writer = Thread(target=insert)
            writer.start()
            writer.join(timeout=10)
            assert not writer.is_alive(), "old backend insert blocked"
            assert not errors, errors

    monkeypatch.setattr(settings, "database_url", db.url)
    logger = logging.getLogger(__name__)
    # The CLI configuration disables application loggers in this pytest worker.
    # Run the real migrations without replacing the caller's logging setup.
    migration_dir = str(Path("alembic").resolve())
    config = Config()
    config.set_main_option("script_location", migration_dir)
    config.set_main_option("prepend_sys_path", migration_dir)
    config.set_main_option("path_separator", "os")
    event.listen(Engine, "after_cursor_execute", after_execute)
    try:
        command.upgrade(config, "d4f30c9e71a2")
    finally:
        event.remove(Engine, "after_cursor_execute", after_execute)
    assert inserted == [True]
    with caplog.at_level(logging.WARNING, logger=__name__):
        logger.warning("migration replay left application logging available")
    assert "migration replay left application logging available" in caplog.text


def _owner(db):
    return db.fetchval(
        'INSERT INTO "user" (username, email, created_at, updated_at) VALUES ($1, $2, now(), now()) RETURNING id',
        "rollout-" + uuid.uuid4().hex[:8],
        uuid.uuid4().hex + "@example.test",
    )


def test_old_connection_insert_during_backfill_gets_a_team(monkeypatch, caplog):
    with database_at("d1f37c8a46b2") as db:
        owner = _owner(db)
        connection = uuid.uuid4()

        def write():
            db.execute(
                "INSERT INTO account_model_connections (id, owner_user_id, kind, key, key_last4, created_at, updated_at) VALUES ($1, $2, 'api_key', 'sealed', '1234', now(), now())",
                connection,
                owner,
            )

        _upgrade_with_old_write(
            db,
            monkeypatch,
            caplog,
            "UPDATE account_model_connections c SET team_id",
            write,
        )
        assert db.fetchval(
            "SELECT team_id FROM account_model_connections WHERE id = $1", connection
        )


def test_old_login_insert_during_backfill_keeps_usable_models(monkeypatch, caplog):
    with database_at("d1f37c8a46b2") as db:
        owner = _owner(db)
        team = db.fetchval(
            "INSERT INTO team (id, name, intro, description, avatar_id, personal_owner_user_id, created_at, updated_at) VALUES (nextval('team_seq'), 'p', '', '', 0, $1, now(), now()) RETURNING id",
            owner,
        )
        device = uuid.uuid4().hex
        db.execute(
            "INSERT INTO device (device_id, name, token, team_id, owner_user_id, created_at) VALUES ($1, 'd', $2, $3, $4, now())",
            device,
            uuid.uuid4().hex,
            team,
            owner,
        )

        def write():
            db.execute(
                "INSERT INTO claude_logins (id, device_id, local_id, installed, logged_in, auth_method, subscription_type, checked_at) VALUES ($1, $1, 'default', true, true, 'claude.ai', 'max', now())",
                device,
            )

        _upgrade_with_old_write(
            db, monkeypatch, caplog, "UPDATE claude_logins SET allowed_models", write
        )
        assert (
            db.fetchval(
                "SELECT allowed_models FROM claude_logins WHERE id = $1", device
            )
            == '["opus", "sonnet", "haiku"]'
        )
