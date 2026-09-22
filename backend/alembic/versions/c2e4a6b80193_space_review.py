"""Add space review state; existing spaces remain approved."""

import sqlalchemy as sa

from alembic import op

revision = "c2e4a6b80193"
down_revision = "b8e3f2a10c64"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "space",
        sa.Column(
            "review_status", sa.String(16), nullable=False, server_default="APPROVED"
        ),
    )
    op.add_column("space", sa.Column("review_reason", sa.Text(), nullable=True))
    op.add_column("space", sa.Column("reviewed_by", sa.String(255), nullable=True))
    op.add_column(
        "space", sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    for column in ("reviewed_at", "reviewed_by", "review_reason", "review_status"):
        op.drop_column("space", column)
