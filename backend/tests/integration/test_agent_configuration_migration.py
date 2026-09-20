"""Upgrade populated roles, defaults and memory identities without data loss."""

import importlib.util
import json
import uuid
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.core.config import settings
from app.domain.project.services import ProjectService


def test_existing_configuration_is_snapshotted_and_originals_are_archived(
    db_session, _portal, monkeypatch
):
    monkeypatch.setattr(settings, "subscription_enabled", True)
    path = (
        Path(__file__).parents[2]
        / "alembic/versions/d7a91c4e2b60_agent_owned_configuration.py"
    )
    spec = importlib.util.spec_from_file_location("agent_config_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    async def run():
        projects = ProjectService(db_session)
        custom = await projects.create(name="Custom")
        plain = await projects.create(name="Plain")
        preset = await projects.create(name="Preset")
        custom_agent_id = custom.default_agent_instance_id
        preset_agent_id = preset.default_agent_instance_id
        await db_session.flush()
        connection = await db_session.connection()

        def check(conn):
            migration.op = Operations(MigrationContext.configure(conn))
            migration.downgrade()
            conn.execute(
                sa.text(
                    "UPDATE agent_instances SET type_name='fullstack-engineer' "
                    "WHERE id=:id"
                ),
                {"id": preset_agent_id},
            )
            conn.execute(
                sa.text(
                    "UPDATE agent_instances SET type_name='custom-review' WHERE id=:id"
                ),
                {"id": custom_agent_id},
            )
            conn.execute(
                sa.text(
                    "UPDATE projects SET default_agent_instance_id=NULL WHERE id=:id"
                ),
                {"id": plain.id},
            )
            conn.execute(
                sa.text("DELETE FROM agent_instances WHERE project_id=:id"),
                {"id": plain.id},
            )
            old_settings = {
                "subscription_model": "fable",
                "sandbox_image": "keep-image",
                "execution_profile": "default",
            }
            conn.execute(
                sa.text(
                    "UPDATE projects SET settings=CAST(:value AS json) "
                    "WHERE id IN (:custom, :plain)"
                ),
                {
                    "value": json.dumps(old_settings),
                    "custom": custom.id,
                    "plain": plain.id,
                },
            )
            conn.execute(
                sa.text(
                    "INSERT INTO agent_types "
                    "(id, name, title, description, body, model, skills, mcp_servers, "
                    "harness, effort, created_by, created_at, updated_at) "
                    "VALUES (:id, 'custom-review', 'Reviewer', '', "
                    "'Keep my instructions', 'opus', '[\"saved-skill\"]', "
                    "'[\"saved-server\"]', 'claude-code', 'high', 'alice', "
                    "now(), now())"
                ),
                {"id": uuid.uuid4()},
            )
            migration.upgrade()
            config = conn.execute(
                sa.text("SELECT configuration FROM agent_instances WHERE id=:id"),
                {"id": custom_agent_id},
            ).scalar_one()
            assert config == {
                "body": "Keep my instructions",
                "model": "opus",
                "skills": ["saved-skill"],
                "mcp_servers": ["saved-server"],
                "harness": "claude-code",
                "effort": "high",
            }
            default = conn.execute(
                sa.text(
                    "SELECT a.handle, a.configuration, p.settings FROM projects p "
                    "JOIN agent_instances a ON a.id=p.default_agent_instance_id "
                    "WHERE p.id=:id"
                ),
                {"id": plain.id},
            ).one()
            assert default.handle == "cheese"
            assert default.configuration["model"] == "fable"
            assert default.settings == {"sandbox_image": "keep-image"}
            preset_config = conn.execute(
                sa.text("SELECT configuration FROM agent_instances WHERE id=:id"),
                {"id": preset_agent_id},
            ).scalar_one()
            assert preset_config["body"] and preset_config["model"] == "sonnet"
            assert (
                conn.execute(
                    sa.text(
                        "SELECT body FROM agent_types_archive "
                        "WHERE name='custom-review'"
                    )
                ).scalar_one()
                == "Keep my instructions"
            )
            assert (
                conn.execute(
                    sa.text(
                        "SELECT settings FROM agent_configuration_migration_backup "
                        "WHERE project_id=:id"
                    ),
                    {"id": custom.id},
                ).scalar_one()
                == old_settings
            )
            assert (
                conn.execute(
                    sa.text("SELECT handle FROM agent_instances WHERE id=:id"),
                    {"id": custom_agent_id},
                ).scalar_one()
                == "cheese"
            )
            migration.downgrade()
            assert (
                conn.execute(
                    sa.text("SELECT settings FROM projects WHERE id=:id"),
                    {"id": custom.id},
                ).scalar_one()
                == old_settings
            )
            assert (
                conn.execute(
                    sa.text(
                        "SELECT configuration FROM agent_configuration_rollback_backup "
                        "WHERE id=:id ORDER BY archived_at DESC LIMIT 1"
                    ),
                    {"id": custom_agent_id},
                ).scalar_one()
                == config
            )
            assert (
                conn.execute(
                    sa.text("SELECT model FROM agent_types WHERE name='custom-review'")
                ).scalar_one()
                == "opus"
            )
            # Restore the current schema before the fixture releases its transaction.
            migration.upgrade()

        await connection.run_sync(check)

    _portal.call(run)
