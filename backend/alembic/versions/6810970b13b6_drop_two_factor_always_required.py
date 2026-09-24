"""Drop user_two_factor.always_required

Sign-in never read the flag and no screen could set it, so it only reported
a setting that did nothing.

Revision ID: 6810970b13b6
Revises: 4b8e1f6c2a93
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "6810970b13b6"
down_revision: str | Sequence[str] | None = "4b8e1f6c2a93"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("user_two_factor", "always_required")


def downgrade() -> None:
    op.add_column(
        "user_two_factor",
        sa.Column(
            "always_required",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
