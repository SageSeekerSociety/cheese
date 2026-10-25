"""A 题目's version records which files it held

Revision ID: 2b398ee3e416
Revises: e5a17c93d2b4
Create Date: 2026-10-25

#4767 §一: a task's history page lists every version with the files it held,
each downloadable. ``task_package_revisions.files`` maps each name in the
task's library at that version to the ``library_files`` row it was then.
Versions recorded before this column stay null: what they held was never
written down, and timestamps cannot reconstruct it (the publish path wrote
version 1 before its files).

A nullable column on a small table: the previous release never reads it.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "2b398ee3e416"
down_revision: str | Sequence[str] | None = "e5a17c93d2b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with_lock_retries("task_package_revisions")
    op.add_column(
        "task_package_revisions",
        sa.Column("files", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    with_lock_retries("task_package_revisions")
    op.drop_column("task_package_revisions", "files")
