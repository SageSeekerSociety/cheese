"""deliveries: asked_attempt_id

The latest attempt at a delivery that a person asked for (「重试」), so a try
they pressed for that fails says so in the conversation, while the platform's
own retries of the same instruction say it once.

Revision ID: c41a7e9d2b58
Revises: d6f2b8a4c1e7
Create Date: 2026-11-02 18:30:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c41a7e9d2b58"
down_revision: str | Sequence[str] | None = "d6f2b8a4c1e7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("deliveries", sa.Column("asked_attempt_id", sa.Uuid(), nullable=True))


def downgrade() -> None:
    op.drop_column("deliveries", "asked_attempt_id")
