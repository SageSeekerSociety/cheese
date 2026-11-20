"""The execution environment is a row of its own

Revision ID: caeeb9d1d8b7
Revises: 2d06b1f1c765

An environment (a machine, the files on it and their lifecycle) is the row a
sandbox's home already was, ``sandbox_homes``, now also carrying what its
executor's install left (``executor``, ``status``, the install's ``claim``)
and a thaw it owes (``thaw_requested_at``). A conversation points to its
default one (``conversation_environments``), and the sessions working in one
now are attached to it (``environment_attachments``). What an agent is owed
about its environment moves to ``agent_sessions.environment_notice``.

Only additions: every new column is nullable or has a server default, so the
previous release reads and writes these tables as before. The rows are filled
by the next migration.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "caeeb9d1d8b7"
down_revision: str | Sequence[str] | None = "2d06b1f1c765"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with_lock_retries("sandbox_homes, agent_sessions, conversations, cloud_hosts")
    op.add_column(
        "sandbox_homes",
        sa.Column("thaw_requested_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "sandbox_homes",
        sa.Column("executor", postgresql.JSONB(), nullable=True),
    )
    op.add_column(
        "sandbox_homes",
        sa.Column(
            "status",
            sa.String(16),
            nullable=False,
            server_default="preparing",
        ),
    )
    op.add_column("sandbox_homes", sa.Column("claim", sa.String(36), nullable=True))
    op.add_column(
        "sandbox_homes",
        sa.Column("claim_until", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "sandbox_homes",
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "agent_sessions",
        sa.Column("environment_notice", postgresql.JSONB(), nullable=True),
    )
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
            ["sandbox_homes.id"],
            ondelete="SET NULL",
            name="fk_conversation_environments_environment_id_sandbox_homes",
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
            ["sandbox_homes.id"],
            ondelete="CASCADE",
            name="fk_environment_attachments_environment_id_sandbox_homes",
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
    op.drop_column("agent_sessions", "environment_notice")
    for column in (
        "verified_at",
        "claim_until",
        "claim",
        "status",
        "executor",
        "thaw_requested_at",
    ):
        op.drop_column("sandbox_homes", column)
