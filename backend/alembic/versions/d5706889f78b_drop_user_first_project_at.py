"""Drop user.first_project_at

Revision ID: d5706889f78b
Revises: 64318b76b3be
Create Date: 2026-10-20

The guided first run records `onboarded_at` instead, and #3392 unmapped
`first_project_at` for a later drop; that release is deployed. On its own, as
`user` is read on every request.

The downgrade adds the column back, empty.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries

from alembic import op

revision: str = "d5706889f78b"
down_revision: str | Sequence[str] | None = "64318b76b3be"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with_lock_retries('"user"')
    op.drop_column("user", "first_project_at")


def downgrade() -> None:
    op.add_column(
        "user",
        sa.Column("first_project_at", sa.DateTime(timezone=True), nullable=True),
    )
