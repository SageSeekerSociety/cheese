"""A file item of a submission holds several files

Revision ID: c4e1a7b93d20
Revises: f02ff6f94b03
Create Date: 2026-11-14

`task_submission_entry_file`: the files handed in under one file item, in the
order the submitter gave them, so a Markdown report goes in with the images it
refers to. An entry written before this keeps its one file in
`task_submission_entry.content_attachment_id`, which the code reads for an
entry with no rows here; nothing is backfilled, so the release still serving
while this runs, which writes that column, loses nothing.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries

from alembic import op

revision: str = "c4e1a7b93d20"
down_revision: str | Sequence[str] | None = "f02ff6f94b03"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FK = "fk_task_submission_entry_file_entry_id_task_submission_entry"


def upgrade() -> None:
    op.create_table(
        "task_submission_entry_file",
        sa.Column("entry_id", sa.BigInteger(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("attachment_id", sa.Integer(), nullable=False),
        # Starts with entry_id, so it is also the foreign key's index.
        sa.PrimaryKeyConstraint("entry_id", "position"),
    )
    # The foreign key locks the table it points at, which the release still
    # serving reads and writes, so it is added after taking both in turn.
    with_lock_retries("task_submission_entry_file, task_submission_entry")
    op.create_foreign_key(
        _FK,
        "task_submission_entry_file",
        "task_submission_entry",
        ["entry_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_table("task_submission_entry_file")
