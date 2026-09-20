"""Snapshot role and model configuration onto each project agent."""

import json
import uuid
from pathlib import Path

import sqlalchemy as sa

from alembic import op

revision = "d7a91c4e2b60"
down_revision = "e2c7a4b97310"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Deployment defaults are read once during conversion and persisted per agent.
    from app.core.config import settings

    bind = op.get_bind()
    presets = json.loads(
        Path(__file__).with_name("d7a91c4e2b60_presets.json").read_text()
    )
    roles = {
        **presets,
        **{
            r["name"]: dict(r)
            for r in bind.execute(sa.text("SELECT * FROM agent_types")).mappings()
        },
    }
    op.add_column(
        "agent_instances", sa.Column("configuration", sa.JSON(), nullable=True)
    )
    # Keep original custom roles and project settings for inspection or rollback.
    op.execute(
        "CREATE TABLE agent_configuration_migration_backup AS SELECT id AS project_id, settings FROM projects"
    )
    for project in (
        bind.execute(
            sa.text("SELECT id, settings, default_agent_instance_id FROM projects")
        )
        .mappings()
        .all()
    ):
        project_settings = project["settings"] or {}
        supply = project_settings.get("supply")
        if supply not in {"subscription", "gateway"}:
            supply = "subscription" if settings.subscription_enabled else "gateway"
        agents = (
            bind.execute(
                sa.text(
                    "SELECT id, handle, type_name FROM agent_instances WHERE project_id=:id"
                ),
                {"id": project["id"]},
            )
            .mappings()
            .all()
        )
        if (
            not any(a["handle"] == "cheese" for a in agents)
            and project["default_agent_instance_id"] is None
        ):
            agent_id = uuid.uuid4()
            bind.execute(
                sa.text(
                    "INSERT INTO agent_instances (id, project_id, handle, display_name, is_active, created_at, updated_at) VALUES (:id, :project, 'cheese', '芝士', true, now(), now())"
                ),
                {"id": agent_id, "project": project["id"]},
            )
            agents.append({"id": agent_id, "handle": "cheese", "type_name": None})
        for agent in agents:
            role = roles.get(agent["type_name"], {})
            model = (
                (
                    role.get("model")
                    or project_settings.get("subscription_model")
                    or "sonnet"
                )
                if supply == "subscription"
                else settings.agent_model
            )
            config = {
                "body": role.get("body") or "",
                "model": model,
                "harness": role.get("harness") or "claude-code",
                "skills": role.get("skills") or [],
                "mcp_servers": role.get("mcp_servers") or [],
                "effort": role.get("effort"),
            }
            bind.execute(
                sa.text(
                    "UPDATE agent_instances SET configuration=CAST(:config AS json) WHERE id=:id"
                ),
                {"id": agent["id"], "config": json.dumps(config)},
            )
            if (
                project["default_agent_instance_id"] is None
                and agent["handle"] == "cheese"
            ):
                bind.execute(
                    sa.text(
                        "UPDATE projects SET default_agent_instance_id=:agent WHERE id=:project"
                    ),
                    {"agent": agent["id"], "project": project["id"]},
                )
                bind.execute(
                    sa.text("UPDATE agent_instances SET is_active=true WHERE id=:id"),
                    {"id": agent["id"]},
                )
    op.alter_column("agent_instances", "configuration", nullable=False)
    op.execute(
        "UPDATE projects SET settings = (COALESCE(settings::jsonb, '{}'::jsonb) - 'subscription_model' - 'execution_profile')::json"
    )
    op.rename_table("agent_types", "agent_types_archive")


def downgrade() -> None:
    # Agent edits made after upgrade cannot be reconstructed from the old roles.
    op.execute(
        "CREATE TABLE IF NOT EXISTS agent_configuration_rollback_backup AS "
        "SELECT id, project_id, handle, configuration, clock_timestamp() AS archived_at "
        "FROM agent_instances WHERE false"
    )
    op.execute(
        "INSERT INTO agent_configuration_rollback_backup "
        "SELECT id, project_id, handle, configuration, clock_timestamp() "
        "FROM agent_instances"
    )
    op.rename_table("agent_types_archive", "agent_types")
    op.execute(
        "UPDATE projects p SET settings = b.settings FROM agent_configuration_migration_backup b WHERE p.id=b.project_id"
    )
    op.drop_table("agent_configuration_migration_backup")
    op.drop_column("agent_instances", "configuration")
