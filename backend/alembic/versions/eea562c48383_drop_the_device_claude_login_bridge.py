"""Drop the one-login-per-machine table and the triggers that bridged it

Revision ID: eea562c48383
Revises: 9e2e92837efc
Create Date: 2026-10-20

`e81c7f29ab40` gave every Claude Code login a row of its own
(`claude_logins`) and an own agent the login it runs on (`own_agents.login_id`),
and kept `device_claude_login`, `own_agents.device_id` and two triggers for the
release still serving while it ran. That release no longer serves (#4408 is
deployed), so the triggers, their functions, the table and the column go.
`own_agents.callers` went unmapped in #4404 and goes with them.

The downgrade puts the table and the columns back empty, with their
constraints and indexes. It does not put the triggers back: they carried the
writes of the release before `e81c7f29ab40`, which no downgrade to here serves.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries

from alembic import op

revision: str = "eea562c48383"
down_revision: str | Sequence[str] | None = "9e2e92837efc"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with_lock_retries("own_agents, device_claude_login, device")
    op.execute(
        "DROP TRIGGER IF EXISTS own_agent_login_from_previous_release ON own_agents"
    )
    op.execute("DROP FUNCTION IF EXISTS own_agent_login_from_previous_release()")
    op.execute(
        "DROP TRIGGER IF EXISTS claude_login_from_previous_release"
        " ON device_claude_login"
    )
    op.execute("DROP FUNCTION IF EXISTS claude_login_from_previous_release()")
    op.drop_constraint("ck_own_agents_one_connection", "own_agents", type_="check")
    op.drop_constraint(
        "fk_own_agents_device_id_device", "own_agents", type_="foreignkey"
    )
    op.drop_index("ix_own_agents_device_id", table_name="own_agents")
    op.drop_column("own_agents", "device_id")
    op.drop_constraint("ck_own_agents_callers", "own_agents", type_="check")
    op.drop_column("own_agents", "callers")
    op.drop_table("device_claude_login")


def downgrade() -> None:
    op.create_table(
        "device_claude_login",
        sa.Column("device_id", sa.String(64), nullable=False),
        sa.Column("installed", sa.Boolean(), nullable=False),
        sa.Column("logged_in", sa.Boolean(), nullable=False),
        sa.Column("auth_method", sa.String(32), nullable=True),
        sa.Column("subscription_type", sa.String(32), nullable=True),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("model", sa.String(128), nullable=True),
        sa.Column("service_host", sa.String(255), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("name", sa.String(64), nullable=True),
        sa.PrimaryKeyConstraint("device_id", name="device_claude_login_pkey"),
        sa.ForeignKeyConstraint(
            ["device_id"],
            ["device.device_id"],
            ondelete="CASCADE",
            name="device_claude_login_device_id_fkey",
        ),
    )
    op.add_column(
        "own_agents",
        sa.Column("callers", sa.String(16), server_default="owner", nullable=False),
    )
    op.create_check_constraint(
        "ck_own_agents_callers", "own_agents", "callers IN ('owner', 'project')"
    )
    op.add_column("own_agents", sa.Column("device_id", sa.String(64), nullable=True))
    op.create_index("ix_own_agents_device_id", "own_agents", ["device_id"])
    op.create_foreign_key(
        "fk_own_agents_device_id_device",
        "own_agents",
        "device",
        ["device_id"],
        ["device_id"],
        ondelete="SET NULL",
    )
    op.create_check_constraint(
        "ck_own_agents_one_connection",
        "own_agents",
        "device_id IS NULL OR account_connection_id IS NULL",
    )
