"""Every change to a project memory file is kept, with what it said before.

`memory_file_changes` holds one row per write or delete of a team or private
memory file: who made it, which writer it came through (a turn's reconcile, the
panel, the dream, a restore, ...), when, and the whole text before and after.
The memory panel reads it as 「最近改动」 and restores a file from a row. Rows
older than 30 days are deleted by the `memory history retention` job.

A new table, written only by the new release: the previous release never reads
it, so it keeps serving during the deploy.

Revision ID: ce900956b682
Revises: a7c3e9d1b204
Create Date: 2026-10-30 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries

from alembic import op

revision: str = "ce900956b682"
down_revision: str | Sequence[str] | None = "a7c3e9d1b204"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Copied from `app.domain.memory.files.MemoryFileScope` and
# `app.domain.memory.writers.MemoryWriter` as they are at this revision.
_SCOPES = ("team", "private", "personal")
_WRITERS = (
    "turn",
    "dream_team",
    "dream_private",
    "panel",
    "restore",
    "skill",
    "migration",
    "assistant",
)


def upgrade() -> None:
    # The new table's foreign key locks `projects`, which the serving backend
    # writes.
    with_lock_retries("projects")
    op.create_table(
        "memory_file_changes",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "project_id",
            sa.Uuid(),
            sa.ForeignKey(
                "projects.id",
                ondelete="CASCADE",
                name="fk_memory_file_changes_project_id_projects",
            ),
            nullable=False,
        ),
        sa.Column(
            "scope",
            sa.Enum(
                *_SCOPES,
                native_enum=False,
                length=8,
                create_constraint=True,
                name="ck_memory_file_changes_scope",
            ),
            nullable=False,
        ),
        sa.Column("owner_handle", sa.String(64), nullable=False, server_default=""),
        sa.Column("path", sa.String(200), nullable=False),
        sa.Column(
            "writer",
            sa.Enum(
                *_WRITERS,
                native_enum=False,
                length=16,
                create_constraint=True,
                name="ck_memory_file_changes_writer",
            ),
            nullable=False,
        ),
        sa.Column("changed_by", sa.String(64), nullable=False),
        sa.Column("before", sa.Text(), nullable=True),
        sa.Column("after", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "before IS NOT NULL OR after IS NOT NULL",
            name="ck_memory_file_changes_something",
        ),
    )
    op.create_index(
        "ix_memory_file_changes_scope_created",
        "memory_file_changes",
        ["project_id", "scope", "owner_handle", "created_at"],
    )
    op.create_index(
        "ix_memory_file_changes_created_at", "memory_file_changes", ["created_at"]
    )


def downgrade() -> None:
    op.drop_table("memory_file_changes")
