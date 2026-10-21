"""accept_cards: who merged the PR on GitHub

The project overview's 「最近进展」 names the person behind each accept. A card
accepted by the platform click has ``decided_by``; one whose PR a human merged
on GitHub had nobody — the poller now records GitHub's ``merged_by`` here.

Nullable with no backfill in SQL: the value is a GitHub fact the database
cannot derive, and the poller's own backfill sweep fills the last 14 days.

Revision ID: 53f2f3deeb83
Revises: b5d1e9a3c7f2
Create Date: 2026-10-21 16:46:45
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "53f2f3deeb83"
down_revision: str | Sequence[str] | None = "b5d1e9a3c7f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("accept_cards", sa.Column("merged_by", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("accept_cards", "merged_by")
