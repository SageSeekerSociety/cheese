"""When each machine was last heard from

Revision ID: b7e3c9d1f4a2
Revises: 7e3c1b9d4a52
Create Date: 2026-10-07

``device.last_seen_at``: written as a machine connects and at most once a
minute while it stays connected, so the devices page can say how long one has
been away.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b7e3c9d1f4a2"
down_revision: str | Sequence[str] | None = "7e3c1b9d4a52"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "device",
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("device", "last_seen_at")
