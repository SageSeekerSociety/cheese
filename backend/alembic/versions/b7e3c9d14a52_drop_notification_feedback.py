"""Notifications no longer carry a thumbs-up or thumbs-down

Revision ID: b7e3c9d14a52
Revises: 7e3c1b9d4a52
Create Date: 2026-10-07

The rating buttons lived on the project board's deck of alerts, which is gone,
and nobody had ever used them: the column was empty on dev. The admin
dashboard's usefulness card that read it goes with it.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b7e3c9d14a52"
down_revision: str | Sequence[str] | None = "7e3c1b9d4a52"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("notification", "feedback")


def downgrade() -> None:
    op.add_column(
        "notification",
        sa.Column("feedback", sa.String(length=8), nullable=True),
    )
