"""accept card PR fields (PR-based accept, #188 §5.1)

Revision ID: b3d5f7a9c102
Revises: a8c2d4e6f901
Create Date: 2026-08-09
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b3d5f7a9c102"
down_revision: str | Sequence[str] | None = "a8c2d4e6f901"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("accept_cards", sa.Column("pr_number", sa.Integer(), nullable=True))
    op.add_column("accept_cards", sa.Column("pr_url", sa.String(255), nullable=True))


def downgrade() -> None:
    op.drop_column("accept_cards", "pr_url")
    op.drop_column("accept_cards", "pr_number")
