"""create ai user quota table

Revision ID: 1c64f1712118
Revises:
Create Date: 2025-12-02 02:57:53.829105

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "1c64f1712118"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ai_user_quota",
        sa.Column("user_id", sa.Integer(), primary_key=True),
        sa.Column(
            "used",
            sa.Float(),
            nullable=False,
            server_default="0",
            comment="Quota consumed within the current reset window.",
        ),
        sa.Column(
            "reset_at",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="Timestamp when quota counters reset to zero.",
        ),
    )


def downgrade() -> None:
    op.drop_table("ai_user_quota")
