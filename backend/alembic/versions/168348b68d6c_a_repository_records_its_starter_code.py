"""A project's repository records the task version its starter code came from

Revision ID: 168348b68d6c
Revises: e6b2d9f4a173
Create Date: 2026-10-24

#4767 §二. A project made from a 题目 with starter code begins with that
code as its repository's first commit; ``project_forges.starter_revision`` is
the 题目's version (``task_package_revisions.number``) the code was taken
from, NULL for a repository that began without one. Nullable, no default:
the previous release never writes it.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries

from alembic import op

revision: str = "168348b68d6c"
down_revision: str | Sequence[str] | None = "e6b2d9f4a173"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with_lock_retries("project_forges")
    op.add_column(
        "project_forges", sa.Column("starter_revision", sa.Integer(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("project_forges", "starter_revision")
