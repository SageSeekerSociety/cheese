"""cloud_host_homes: drop left_at

Nothing has written it since a session that moves on gives its home up, and
the previous release stopped mapping it.

Revision ID: ff58bbb04b89
Revises: 4b7e2d9c1f30
Create Date: 2026-10-07 23:30:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "ff58bbb04b89"
down_revision: str | Sequence[str] | None = "4b7e2d9c1f30"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("cloud_host_homes", "left_at")


def downgrade() -> None:
    op.add_column(
        "cloud_host_homes",
        sa.Column("left_at", sa.DateTime(timezone=True), nullable=True),
    )
