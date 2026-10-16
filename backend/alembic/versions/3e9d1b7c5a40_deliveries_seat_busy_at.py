"""deliveries: seat_busy_at, when an attempt last found its seat busy

A delivery refused because its agent's seat still holds an earlier input is
waiting for that work to end; its give-up clock counts from this moment rather
than from when it was given. Nullable, no backfill: a row refused before this
release has no busy attempt on record and keeps the old clock.

Revision ID: 3e9d1b7c5a40
Revises: 7c3e5a1f9b24
Create Date: 2026-10-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries

from alembic import op

revision: str = "3e9d1b7c5a40"
down_revision: str | Sequence[str] | None = "7c3e5a1f9b24"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with_lock_retries("deliveries")
    op.add_column(
        "deliveries",
        sa.Column("seat_busy_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("deliveries", "seat_busy_at")
