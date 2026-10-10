"""Whether a machine a person enrolled can give a session a sandbox

Revision ID: 70c0b60a075a
Revises: 8083d813e832
Create Date: 2026-10-10

``device_sandbox``: one row per machine, what it last answered when asked to
make the sandbox its install makes for a session (`device.sandbox`).
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "70c0b60a075a"
down_revision: str | Sequence[str] | None = "8083d813e832"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "device_sandbox",
        sa.Column("device_id", sa.String(64), nullable=False),
        sa.Column("isolates", sa.Boolean(), nullable=False),
        sa.Column("reason", sa.String(1000), nullable=True),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["device_id"], ["device.device_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("device_id"),
    )


def downgrade() -> None:
    op.drop_table("device_sandbox")
