"""accept_cards.decided_by_seat: which AI teammate adopted a delivery for its reviewer

Revision ID: e3c9a7f21b54
Revises: d7a2e41f8b06
Create Date: 2026-11-17

A channel's main line adopts a delivery on its reviewer's word
(`accept_coordinated_task`); the card already says an assistant decided it
(`decided_by_assistant`), and now also which project teammate's seat did, so
the line recording the adoption names that teammate instead of guessing one.
NULL for every card a person decided, for one their own personal assistant
decided, and for every card decided before this column existed.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries

from alembic import op

revision: str = "e3c9a7f21b54"
down_revision: str | Sequence[str] | None = "d7a2e41f8b06"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with_lock_retries("accept_cards")
    op.add_column(
        "accept_cards", sa.Column("decided_by_seat", sa.String(64), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("accept_cards", "decided_by_seat")
