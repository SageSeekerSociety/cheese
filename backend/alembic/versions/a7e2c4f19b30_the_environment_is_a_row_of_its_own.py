"""The execution environment is a row of its own

Revision ID: a7e2c4f19b30
Revises: 2d06b1f1c765

An execution environment (a machine, the files on it and their lifecycle) gets
its own table, ``environments``, with what its executor's install left
(``executor``), whether that executor answers (``status``) and the install
running in it (``claim``). A sandbox stays a ``sandbox_homes`` row, which an
environment on a sandbox names. A conversation points to its default
environment (``conversation_environments``), and the agent sessions working
in one now are attached to it (``environment_attachments``). An environment
records the choice it was made for (``choice``, the shape of
``agent_environments.environment``), so a later attacher with the same choice
finds it. A tool call's request to wake a frozen sandbox is the sandbox's
(``sandbox_homes.thaw_wanted_at``). What an agent is
owed about its environment gets a column of its own,
``agent_sessions.environment_notice``.

Only additions: the previous release reads and writes every existing table as
before, and never reads the new ones. The rows are filled by a later
migration.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "a7e2c4f19b30"
down_revision: str | Sequence[str] | None = "2d06b1f1c765"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with_lock_retries(
        "agent_sessions, conversations, projects, cloud_hosts, sandbox_homes"
    )
    op.add_column(
        "agent_sessions",
        sa.Column("environment_notice", postgresql.JSONB(), nullable=True),
    )
    op.add_column(
        "sandbox_homes",
        sa.Column("thaw_wanted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "environments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("topic_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column("device_id", sa.String(64), nullable=True),
        sa.Column("host_id", sa.Uuid(), nullable=True),
        sa.Column("sandbox_home_id", sa.Uuid(), nullable=True),
        sa.Column("resource_id", sa.String(36), nullable=False),
        sa.Column("room_resource_id", sa.String(36), nullable=False),
        sa.Column("choice", postgresql.JSONB(), nullable=False),
        sa.Column(
            "executor",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("status", sa.String(16), nullable=False, server_default="preparing"),
        sa.Column("claim", sa.String(36), nullable=True),
        sa.Column("claim_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["topic_id"],
            ["conversations.id"],
            ondelete="CASCADE",
            name="fk_environments_topic_id_conversations",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            ondelete="CASCADE",
            name="fk_environments_project_id_projects",
        ),
        sa.ForeignKeyConstraint(
            ["host_id"],
            ["cloud_hosts.id"],
            ondelete="SET NULL",
            name="fk_environments_host_id_cloud_hosts",
        ),
        sa.ForeignKeyConstraint(
            ["sandbox_home_id"],
            ["sandbox_homes.id"],
            ondelete="SET NULL",
            name="fk_environments_sandbox_home_id_sandbox_homes",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("topic_id", "project_id", "device_id", "host_id", "sandbox_home_id"):
        op.create_index(f"ix_environments_{column}", "environments", [column])
    op.create_table(
        "conversation_environments",
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("environment_id", sa.Uuid(), nullable=True),
        sa.Column("left_host_id", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            ondelete="CASCADE",
            name="fk_conversation_environments_conversation_id_conversations",
        ),
        sa.ForeignKeyConstraint(
            ["environment_id"],
            ["environments.id"],
            ondelete="SET NULL",
            name="fk_conversation_environments_environment_id_environments",
        ),
        sa.ForeignKeyConstraint(
            ["left_host_id"],
            ["cloud_hosts.id"],
            ondelete="SET NULL",
            name="fk_conversation_environments_left_host_id_cloud_hosts",
        ),
        sa.PrimaryKeyConstraint("conversation_id"),
    )
    op.create_index(
        "ix_conversation_environments_environment_id",
        "conversation_environments",
        ["environment_id"],
    )
    op.create_index(
        "ix_conversation_environments_left_host_id",
        "conversation_environments",
        ["left_host_id"],
    )
    op.create_table(
        "environment_attachments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("environment_id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("generation", sa.String(36), nullable=False),
        sa.Column("setup_digest", sa.String(64), nullable=True),
        sa.Column("acted_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["environment_id"],
            ["environments.id"],
            ondelete="CASCADE",
            name="fk_environment_attachments_environment_id_environments",
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["agent_sessions.id"],
            ondelete="CASCADE",
            name="fk_environment_attachments_session_id_agent_sessions",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id", name="uq_environment_attachments_session_id"),
    )
    op.create_index(
        "ix_environment_attachments_environment_id",
        "environment_attachments",
        ["environment_id"],
    )


def downgrade() -> None:
    op.drop_table("environment_attachments")
    op.drop_table("conversation_environments")
    op.drop_table("environments")
    op.drop_column("sandbox_homes", "thaw_wanted_at")
    op.drop_column("agent_sessions", "environment_notice")
