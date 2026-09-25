"""Give each sign-in a sudo window

Until when a session may get sudo tickets without proving a credential again.

Revision ID: 90e599b0ba34
Revises: dc16d4494b28
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "90e599b0ba34"
down_revision: str | Sequence[str] | None = "dc16d4494b28"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "user_sessions",
        sa.Column("sudo_until", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("user_sessions", "sudo_until")
