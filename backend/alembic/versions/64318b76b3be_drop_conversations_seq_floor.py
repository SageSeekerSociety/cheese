"""Drop conversations.seq_floor

Revision ID: 64318b76b3be
Revises: 8f81c3860e35
Create Date: 2026-10-20

Every block has its number, and the numbering trigger takes the next one from
the largest alone, so #3218 unmapped the floor and left the drop to a later
release. That release is deployed. On its own: `conversations` is among the
busiest tables, and its lock is not held together with any other.

The downgrade adds the column back at its default, 0.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries

from alembic import op

revision: str = "64318b76b3be"
down_revision: str | Sequence[str] | None = "8f81c3860e35"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with_lock_retries("conversations")
    op.drop_column("conversations", "seq_floor")


def downgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column("seq_floor", sa.BigInteger(), server_default="0", nullable=False),
    )
