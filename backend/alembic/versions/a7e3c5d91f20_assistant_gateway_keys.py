"""A person's own gateway key for their 芝士

Revision ID: a7e3c5d91f20
Revises: 2733a598f271
Create Date: 2026-10-02

One new table, nothing existing is touched: per person, the virtual gateway key
every model call of their 芝士 is made on, and how far its spend has been
charged to their personal credits.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "a7e3c5d91f20"
down_revision: str | Sequence[str] | None = "2733a598f271"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "assistant_gateway_keys",
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("user.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("usage_ckpt", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("assistant_gateway_keys")
