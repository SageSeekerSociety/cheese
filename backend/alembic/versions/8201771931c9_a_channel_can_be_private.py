"""A channel can be private: only the people in it see it

Revision ID: 8201771931c9
Revises: 7ca6c79ea15b
Create Date: 2026-10-06

Every channel stays public: the column starts false everywhere.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "8201771931c9"
down_revision: str | Sequence[str] | None = "7ca6c79ea15b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "topics",
        sa.Column("members_only", sa.Boolean(), nullable=False, server_default="false"),
    )


def downgrade() -> None:
    op.drop_column("topics", "members_only")
