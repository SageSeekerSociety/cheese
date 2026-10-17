"""Remote MCP servers added in settings; local ones' runs and approvals

Revision ID: c7d41e9a2f05
Revises: 7c3e5a1f9b24
Create Date: 2026-10-17

A project's remote MCP servers came only from its repository's `.mcp.json` and
its teammates' types. ``project_mcp_servers`` holds the ones added in project
settings; a header value such a server sends is a project secret like any other
(``project_mcp_secrets``). ``project_mcp_runs`` keeps how each local server (a
command, run on the room's machine) last fared, and ``project_mcp_approvals``
whether the owner of a member's computer lets one of the project's commands run
on it. New tables, nothing existing rewritten.
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
    # The foreign keys lock `projects` and `device` briefly; safe to repeat.
    with_lock_retries("projects")
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "project_mcp_servers" not in tables:
        _servers()
    if "project_mcp_runs" not in tables:
        _runs()
    if "project_mcp_approvals" not in tables:
        _approvals()


def _project(table: str) -> sa.Column:
    return sa.Column(
        "project_id",
        sa.Uuid(),
        sa.ForeignKey(
            "projects.id", ondelete="CASCADE", name=f"fk_{table}_project_id_projects"
        ),
        nullable=False,
    )


def _stamps() -> list[sa.Column]:
    return [
        sa.Column(
            name,
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        )
        for name in ("created_at", "updated_at")
    ]


def _servers() -> None:
    op.create_table(
        "project_mcp_servers",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _project("project_mcp_servers"),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("header_name", sa.String(128), nullable=True),
        sa.Column("added_by", sa.String(64), nullable=False),
        *_stamps(),
        sa.UniqueConstraint("project_id", "name", name="uq_project_mcp_server"),
    )
    op.create_index(
        "ix_project_mcp_servers_project_id", "project_mcp_servers", ["project_id"]
    )


def _runs() -> None:
    op.create_table(
        "project_mcp_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _project("project_mcp_runs"),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("log", sa.Text(), nullable=False, server_default=""),
        sa.Column("device_id", sa.String(64), nullable=True),
        *_stamps(),
        sa.UniqueConstraint("project_id", "name", name="uq_project_mcp_run"),
    )
    op.create_index(
        "ix_project_mcp_runs_project_id", "project_mcp_runs", ["project_id"]
    )


def _approvals() -> None:
    with_lock_retries("device")
    op.create_table(
        "project_mcp_approvals",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _project("project_mcp_approvals"),
        sa.Column(
            "device_id",
            sa.String(64),
            sa.ForeignKey(
                "device.device_id",
                ondelete="CASCADE",
                name="fk_project_mcp_approvals_device_id_device",
            ),
            nullable=False,
        ),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("command", sa.Text(), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("author", sa.String(128), nullable=True),
        sa.Column("allowed", sa.Boolean(), nullable=True),
        sa.Column("decided_by", sa.String(64), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        *_stamps(),
        sa.UniqueConstraint(
            "project_id", "device_id", "fingerprint", name="uq_project_mcp_approval"
        ),
    )
    op.create_index(
        "ix_project_mcp_approvals_project_id", "project_mcp_approvals", ["project_id"]
    )
    op.create_index(
        "ix_project_mcp_approvals_device_id", "project_mcp_approvals", ["device_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_project_mcp_approvals_device_id", "project_mcp_approvals")
    op.drop_index("ix_project_mcp_approvals_project_id", "project_mcp_approvals")
    op.drop_table("project_mcp_approvals")
    op.drop_index("ix_project_mcp_runs_project_id", "project_mcp_runs")
    op.drop_table("project_mcp_runs")
    op.drop_index("ix_project_mcp_servers_project_id", "project_mcp_servers")
    op.drop_table("project_mcp_servers")
