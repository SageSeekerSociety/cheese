"""A person's own memory, on the same memory files as a project's

Revision ID: d4f8b2a6e931
Revises: a7c3e9f15b28
Create Date: 2026-10-22

``memory_files`` gains a third scope, ``personal``: what a person's own 芝士
remembers about them, held by the person and in no project, so its rows have
no ``project_id``. The unique index over (project, scope, owner, path) cannot
hold rows whose project is NULL (NULL never equals NULL), so the personal ones
get their own partial unique index over (owner, path).

``assistant_rules.memory`` is whether the person's 芝士 uses and keeps that
memory; on until they turn it off.

The previous release keeps reading both tables while this runs: it never
reads a row without a project (every read it makes names one), and it does not
map the new column.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries

from alembic import op

revision: str = "d4f8b2a6e931"
down_revision: str | Sequence[str] | None = "a7c3e9f15b28"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with_lock_retries("memory_files, assistant_rules")
    op.alter_column("memory_files", "project_id", nullable=True)
    # migration-safety: allow index-not-concurrent — memory_files holds a few hundred rows
    op.create_index(
        "uq_memory_files_personal_path",
        "memory_files",
        ["owner_handle", "path"],
        unique=True,
        postgresql_where=sa.text("scope = 'personal'"),
    )
    op.add_column(
        "assistant_rules",
        sa.Column("memory", sa.Boolean(), nullable=False, server_default=sa.true()),
    )


def downgrade() -> None:
    op.drop_column("assistant_rules", "memory")
    op.drop_index("uq_memory_files_personal_path", table_name="memory_files")
    op.execute("DELETE FROM memory_files WHERE project_id IS NULL")
    op.alter_column("memory_files", "project_id", nullable=False)
