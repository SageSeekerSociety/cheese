"""accept_cards.change_subject / change_body — the PR title and body 芝士 wrote

Before this, a topic's PR was titled with the topic's own Chinese title and
bodied with routing bookkeeping, and the squash commit that landed on main
inherited both. Existing cards get NULL and keep the derived-from-topic
fallback, so nothing in flight changes shape.

Revision ID: c1a5f70b3d24
Revises: b9d2e7a4c1f8
Create Date: 2026-08-16
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c1a5f70b3d24"
down_revision: str | Sequence[str] | None = "b9d2e7a4c1f8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "accept_cards", sa.Column("change_subject", sa.String(255), nullable=True)
    )
    op.add_column("accept_cards", sa.Column("change_body", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("accept_cards", "change_body")
    op.drop_column("accept_cards", "change_subject")
