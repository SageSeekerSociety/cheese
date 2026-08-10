"""resource_usage.route — the supply the turn's traffic actually took

Revision ID: a7c31e05d442
Revises: b5045bf862fe
Create Date: 2026-08-10 16:20:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a7c31e05d442"
down_revision: str | Sequence[str] | None = "b5045bf862fe"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # "gateway" | "subscription" | "native"; "" on rows that predate the column
    # (issue #218 — usage rows must name the meter that produced them).
    op.add_column(
        "resource_usage",
        sa.Column("route", sa.String(16), nullable=False, server_default=""),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("resource_usage", "route")
