"""Store the team's default compute target.

Revision ID: a8c2d4e6f901
Revises: c7f4a1b93e28
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a8c2d4e6f901"
down_revision: str | Sequence[str] | None = "c7f4a1b93e28"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "team", sa.Column("compute_profile", sa.String(length=64), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("team", "compute_profile")
