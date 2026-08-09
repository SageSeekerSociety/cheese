"""device_team: device↔team bindings (compute belongs to the team, v4)

A device bound to a team is usable by every project of that team (为团队注册设备).
Parallel to device_project; the device's owner manages these. Additive only.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c9a4e2f7b118"
down_revision: str | Sequence[str] | None = "b3d81f6a2c40"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "device_team",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("device_id", sa.String(length=64), nullable=False),
        sa.Column("team_id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["device_id"], ["device.device_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("device_id", "team_id", name="uq_device_team"),
    )
    op.create_index(
        op.f("ix_device_team_device_id"), "device_team", ["device_id"], unique=False
    )
    op.create_index(
        op.f("ix_device_team_team_id"), "device_team", ["team_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_device_team_team_id"), table_name="device_team")
    op.drop_index(op.f("ix_device_team_device_id"), table_name="device_team")
    op.drop_table("device_team")
