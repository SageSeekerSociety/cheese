"""Local MCP servers added in settings

Revision ID: f3c3866d9926
Revises: c7d41e9a2f05
Create Date: 2026-10-17

Project settings could add only remote MCP servers; a command had to be
committed to the repository's `.mcp.json`. ``project_mcp_commands`` holds the
commands added in settings. An owner asked to let one run on their computer
reads where it is written, so ``project_mcp_approvals`` gains ``source``; the
rows it has are all the repository's.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "f3c3866d9926"
down_revision: str | Sequence[str] | None = "c7d41e9a2f05"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The foreign key locks `projects` briefly; safe to repeat.
    with_lock_retries("projects, project_mcp_approvals")
    inspector = sa.inspect(op.get_bind())
    if "source" not in {
        c["name"] for c in inspector.get_columns("project_mcp_approvals")
    }:
        op.add_column(
            "project_mcp_approvals",
            sa.Column(
                "source", sa.String(16), server_default="repository", nullable=False
            ),
        )
    if "project_mcp_commands" in inspector.get_table_names():
        return
    op.create_table(
        "project_mcp_commands",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "project_id",
            sa.Uuid(),
            sa.ForeignKey(
                "projects.id",
                ondelete="CASCADE",
                name="fk_project_mcp_commands_project_id_projects",
            ),
            nullable=False,
        ),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("command", sa.Text(), nullable=False),
        sa.Column("args", postgresql.JSONB(), nullable=False),
        sa.Column("env", postgresql.JSONB(), nullable=False),
        sa.Column("added_by", sa.String(64), nullable=False),
        *[
            sa.Column(
                name,
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            )
            for name in ("created_at", "updated_at")
        ],
        sa.UniqueConstraint("project_id", "name", name="uq_project_mcp_command"),
    )
    op.create_index(
        "ix_project_mcp_commands_project_id", "project_mcp_commands", ["project_id"]
    )


def downgrade() -> None:
    op.drop_column("project_mcp_approvals", "source")
    op.drop_index("ix_project_mcp_commands_project_id", "project_mcp_commands")
    op.drop_table("project_mcp_commands")
