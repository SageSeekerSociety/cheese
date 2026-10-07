"""An accept card's poll is claimed, not locked

Revision ID: 7e3c1b9d4a52
Revises: a4d8e2f61c07
Create Date: 2026-10-07

``accept_cards.poll_claim`` / ``poll_claimed_until``: which poll is advancing
the card and until when. The poller used to hold ``FOR UPDATE`` on the card
for the whole forge poll, keeping a database connection checked out while it
waited on GitHub. A claim keeps one advancer per card without holding either.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "7e3c1b9d4a52"
down_revision: str | Sequence[str] | None = "a4d8e2f61c07"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("accept_cards", sa.Column("poll_claim", sa.Uuid(), nullable=True))
    op.add_column(
        "accept_cards",
        sa.Column("poll_claimed_until", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("accept_cards", "poll_claimed_until")
    op.drop_column("accept_cards", "poll_claim")
