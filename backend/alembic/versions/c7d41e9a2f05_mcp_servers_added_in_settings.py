"""Remote MCP servers can be added in project settings

Revision ID: c7d41e9a2f05
Revises: 7c3e5a1f9b24
Create Date: 2026-10-17

A project's remote MCP servers came only from its repository's `.mcp.json` and
its teammates' types. ``project_mcp_servers`` holds the ones added in project
settings; a header value such a server sends is a project secret like any other
(``project_mcp_secrets``). A new table, nothing existing rewritten.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries

from alembic import op

revision: str = "c7d41e9a2f05"
down_revision: str | Sequence[str] | None = "7c3e5a1f9b24"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The foreign key locks `projects` briefly; safe to repeat.
    with_lock_retries("projects")
    if "project_mcp_servers" in set(sa.inspect(op.get_bind()).get_table_names()):
        return
    op.create_table(
        "project_mcp_servers",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "project_id",
            sa.Uuid(),
            sa.ForeignKey(
                "projects.id",
                ondelete="CASCADE",
                name="fk_project_mcp_servers_project_id_projects",
            ),
            nullable=False,
        ),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("header_name", sa.String(128), nullable=True),
        sa.Column("added_by", sa.String(64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("project_id", "name", name="uq_project_mcp_server"),
    )
    op.create_index(
        "ix_project_mcp_servers_project_id", "project_mcp_servers", ["project_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_project_mcp_servers_project_id", "project_mcp_servers")
    op.drop_table("project_mcp_servers")
