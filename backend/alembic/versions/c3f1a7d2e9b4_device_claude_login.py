"""Whether a machine's owner has logged in their own Claude Code for the platform

Revision ID: c3f1a7d2e9b4
Revises: d19fe113a345
Create Date: 2026-10-07

``device_claude_login``: one row per machine, what it last answered about the
Claude Code login its owner gave the platform (`cheesehost claude login`).
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c3f1a7d2e9b4"
down_revision: str | Sequence[str] | None = "d19fe113a345"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "device_claude_login",
        sa.Column("device_id", sa.String(64), nullable=False),
        sa.Column("installed", sa.Boolean(), nullable=False),
        sa.Column("logged_in", sa.Boolean(), nullable=False),
        sa.Column("auth_method", sa.String(32), nullable=True),
        sa.Column("subscription_type", sa.String(32), nullable=True),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["device_id"], ["device.device_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("device_id"),
    )


def downgrade() -> None:
    op.drop_table("device_claude_login")
