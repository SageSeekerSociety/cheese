"""accept_cards.handed_in_by: the member who handed a version in themselves

A member can hand a task's changes in as a version of its delivery, filing it
or moving the one waiting. The card keeps who did, so whoever is told what
was adopted is told whose version it is. Every card before this was filed by
the task's AI teammate or moved by a member without a record; both read NULL.

Revision ID: f3b8d2a61c47
Revises: e5a1c7b3d902
Create Date: 2026-11-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries

from alembic import op

revision: str = "f3b8d2a61c47"
down_revision: str | Sequence[str] | None = "e5a1c7b3d902"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with_lock_retries("accept_cards")
    op.add_column(
        "accept_cards",
        sa.Column("handed_in_by", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("accept_cards", "handed_in_by")
