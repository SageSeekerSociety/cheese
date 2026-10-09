"""topic_read_states: drop the time cursor

Revision ID: 74916c1948ae
Revises: 4e8f1c1e2b22
Create Date: 2026-10-09

A read cursor is a block number (`last_read_seq`, `4e8f1c1e2b22`). The release
that made it so stopped mapping `last_read_at`; this is the release after it.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries

from alembic import op

revision: str = "74916c1948ae"
down_revision: str | Sequence[str] | None = "4e8f1c1e2b22"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with_lock_retries("topic_read_states")
    op.drop_column("topic_read_states", "last_read_at")


def downgrade() -> None:
    with_lock_retries("topic_read_states")
    op.add_column(
        "topic_read_states",
        sa.Column("last_read_at", sa.DateTime(timezone=True), nullable=True),
    )
