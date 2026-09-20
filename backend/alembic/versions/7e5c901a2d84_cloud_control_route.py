"""Persist which cloud devices require the host control tunnel."""

import sqlalchemy as sa

from alembic import op

revision = "7e5c901a2d84"
down_revision = "f1a72c8d4e93"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "device",
        sa.Column(
            "cloud_control_private",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("device", "cloud_control_private")
