"""device_health: failure streak + quarantine per compute machine (#186)

Revision ID: b7e3c19d4f80
Revises: c8b1f4a70d29
Create Date: 2026-08-12

One row per device, written only when a HOST-SCOPED turn failure happens and
deleted as soon as a turn gets through, so the table stays empty in the healthy
case. The device FK cascades: a removed machine takes its health record with it.
"""

import sqlalchemy as sa

from alembic import op

revision = "b7e3c19d4f80"
down_revision = "c8b1f4a70d29"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "device_health",
        sa.Column("device_id", sa.String(length=64), nullable=False),
        sa.Column(
            "consecutive_failures", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("last_failure_code", sa.String(length=64), nullable=True),
        sa.Column("last_failure_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("quarantined_until", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["device_id"], ["device.device_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("device_id"),
    )


def downgrade() -> None:
    op.drop_table("device_health")
