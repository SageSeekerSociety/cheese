"""The old machine choice goes: rooms', tasks' and projects' compute settings,
the room pin, and the choice a session carried; and the homes kept only on
the platform's hosts

Revision ID: c4e9a2f7d153
Revises: d7a3e5b1c829
Create Date: 2026-10-12

The release this follows reads each agent's execution environment
(`agent_environments`) and the defaults in `topics.default_environment`,
`tasks.default_environment` and `settings.execution_environment`, and nothing
reads what they replaced. The release before it kept writing the old choice
until it stopped, so the backfill (8b2f4d6a1c37) runs once more first; it
fills only what is still empty. Then:

- `topics.compute_config`, `topics.compute_profile`, `tasks.compute_config`
  and the `device_topic` table are dropped;
- `compute_configs` and `compute_profile` leave every project's settings;
- `choice` and `authorized_by` leave every session's `execution_request`.

Homes are kept in `sandbox_homes` (d7a3e5b1c829) on any machine. The homes the
outgoing release made in `cloud_host_homes` are copied across once more, by
the same statement, and the old table is dropped.
"""

import importlib.util
from collections.abc import Sequence
from pathlib import Path

import sqlalchemy as sa
from migration_helpers import with_lock_retries

from alembic import op

revision: str = "c4e9a2f7d153"
down_revision: str | Sequence[str] | None = "d7a3e5b1c829"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _sibling(name: str):
    path = Path(__file__).with_name(f"{name}.py")
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def upgrade() -> None:
    with_lock_retries("topics, tasks, device_topic, device, cloud_host_homes")
    _sibling("8b2f4d6a1c37_backfill_agent_environments").upgrade()
    op.execute(_sibling("d7a3e5b1c829_sandbox_homes_on_every_machine").COPY)
    op.drop_table("cloud_host_homes")
    op.drop_column("topics", "compute_config")
    op.drop_column("topics", "compute_profile")
    op.drop_column("tasks", "compute_config")
    op.drop_index("ix_device_topic_device_id", table_name="device_topic")
    op.drop_table("device_topic")
    # `settings` and `execution_request` are json, not jsonb, and the
    # key-delete operator is jsonb-only.
    op.execute(
        """
        UPDATE projects
        SET settings = (settings::jsonb - 'compute_configs' - 'compute_profile')::json
        WHERE settings::jsonb ?| array['compute_configs', 'compute_profile']
        """
    )
    op.execute(
        """
        UPDATE agent_sessions
        SET execution_request = (
          execution_request::jsonb - 'choice' - 'authorized_by'
        )::json
        WHERE execution_request::jsonb ?| array['choice', 'authorized_by']
        """
    )


def downgrade() -> None:
    """The columns and the tables come back empty: the choices they held were
    carried into the environments, and the homes into ``sandbox_homes``, which
    the release before this one reads."""
    op.create_table(
        "cloud_host_homes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("host_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("topic_id", sa.Uuid(), nullable=False),
        sa.Column("room_resource_id", sa.String(length=36), nullable=False),
        sa.Column("resource_id", sa.String(length=36), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=True),
        sa.Column("waiting_since", sa.DateTime(timezone=True), nullable=True),
        sa.Column("active_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("stopped_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["host_id"], ["cloud_hosts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["topic_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_cloud_host_homes_host_id", "cloud_host_homes", ["host_id"])
    op.create_index("ix_cloud_host_homes_topic_id", "cloud_host_homes", ["topic_id"])
    op.create_index(
        "uq_cloud_host_homes_current_session",
        "cloud_host_homes",
        ["session_id"],
        unique=True,
        postgresql_where=sa.text("session_id IS NOT NULL"),
    )
    op.create_table(
        "device_topic",
        sa.Column("topic_id", sa.Uuid(), nullable=False),
        sa.Column("device_id", sa.String(length=64), nullable=False),
        sa.Column(
            "visibility",
            sa.String(length=16),
            nullable=False,
            server_default="isolated",
        ),
        sa.ForeignKeyConstraint(
            ["device_id"], ["device.device_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("topic_id"),
    )
    op.create_index(
        "ix_device_topic_device_id", "device_topic", ["device_id"], unique=False
    )
    op.add_column("tasks", sa.Column("compute_config", sa.JSON(), nullable=True))
    op.add_column(
        "topics", sa.Column("compute_profile", sa.String(length=64), nullable=True)
    )
    op.add_column("topics", sa.Column("compute_config", sa.JSON(), nullable=True))
