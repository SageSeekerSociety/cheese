"""Widen space.intro to Text.

Legacy data contains space intros longer than 255 chars (up to 339); the
original String(255) limit would truncate real data on migration. Widen to
unlimited Text to preserve it.

Revision ID: f6b7c8d9e0a1
Revises: e5a6b7c8d9e0
Create Date: 2026-06-07 04:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f6b7c8d9e0a1"
down_revision: str | Sequence[str] | None = "e5a6b7c8d9e0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "space",
        "intro",
        existing_type=sa.String(length=255),
        type_=sa.Text(),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "space",
        "intro",
        existing_type=sa.Text(),
        type_=sa.String(length=255),
        existing_nullable=False,
    )
