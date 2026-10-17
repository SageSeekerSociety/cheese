"""Tasks have no start

Revision ID: 49a2e86fa493
Revises: e4f7009313c1
Create Date: 2026-10-15

#3813 took 「开始」 out: a task is worked from the moment it is made, and the
columns recording who started it, when and at which document version were
left unmapped for one release, while the image before it still read them.
That image is gone, so the columns go.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries

from alembic import op

revision: str = "49a2e86fa493"
down_revision: str | Sequence[str] | None = "e4f7009313c1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with_lock_retries("tasks")
    op.drop_column("tasks", "started_doc_version")
    op.drop_column("tasks", "started_by")
    op.drop_column("tasks", "started_at")


def downgrade() -> None:
    op.add_column(
        "tasks", sa.Column("started_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("tasks", sa.Column("started_by", sa.String(64), nullable=True))
    op.add_column(
        "tasks", sa.Column("started_doc_version", sa.Integer(), nullable=True)
    )
