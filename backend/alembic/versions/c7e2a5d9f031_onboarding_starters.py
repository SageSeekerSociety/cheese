"""the new-user starters: which 题目 the welcome page hands out

Revision ID: c7e2a5d9f031
Revises: b7e2c4a91f30
Create Date: 2026-11-08 12:00:00.000000

A new table, read only by the release this deploy brings in, so the release
still serving never sees it.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c7e2a5d9f031"
down_revision: str | Sequence[str] | None = "b7e2c4a91f30"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "onboarding_starters",
        sa.Column("key", sa.String(32), primary_key=True),
        sa.Column("space_id", sa.BigInteger(), nullable=False),
        sa.Column("task_id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("onboarding_starters")
