"""Remember the refresh token a browser never received

A sign-in whose last rotation never reached the browser is given a new
successor; the undelivered one is kept so that a later use of it still shows
two holders.

Revision ID: 3e8b1c7d9a52
Revises: c5e8a1f47b20
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "3e8b1c7d9a52"
down_revision: str | Sequence[str] | None = "c5e8a1f47b20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "user_sessions",
        sa.Column("abandoned_hash", sa.String(length=64), nullable=True),
    )
    op.create_index(
        op.f("ix_user_sessions_abandoned_hash"),
        "user_sessions",
        ["abandoned_hash"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_user_sessions_abandoned_hash"), table_name="user_sessions")
    op.drop_column("user_sessions", "abandoned_hash")
