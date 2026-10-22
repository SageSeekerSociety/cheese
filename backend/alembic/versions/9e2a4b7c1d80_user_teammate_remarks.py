"""user_teammate_remarks — per-viewer personal remarks for teammates

Revision ID: 9e2a4b7c1d80
Revises: 7c3e5a1f9b24
Create Date: 2026-10-17
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "9e2a4b7c1d80"
down_revision: str | None = "b7e4c9a21d68"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_teammate_remarks",
        sa.Column("viewer_handle", sa.String(length=64), nullable=False),
        sa.Column("target_handle", sa.String(length=64), nullable=False),
        sa.Column("remark", sa.String(length=64), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("viewer_handle", "target_handle"),
    )


def downgrade() -> None:
    op.drop_table("user_teammate_remarks")
