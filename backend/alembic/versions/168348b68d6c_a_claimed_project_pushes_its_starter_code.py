"""A claimed project's repository gets its 题目's starter code in the background

Revision ID: 168348b68d6c
Revises: b7d4e1a9c2f3
Create Date: 2026-10-24

#4767 §二. Claiming a 题目 with starter code records which archive the
claim took (``library_file_id``, the library row of that moment) and the
题目's version, and commits; a background job unpacks it and pushes it as the
repository's first commit, so the claim never holds its transaction while a
large archive is pushed. ``state`` is pending, done or failed; ``error`` is
the sentence a failed push is told with. A new table: the previous release
never reads it.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "168348b68d6c"
down_revision: str | Sequence[str] | None = "b7d4e1a9c2f3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with_lock_retries("projects, library_files")
    op.create_table(
        "project_starter_pushes",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("library_file_id", sa.Uuid(), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claim_token", sa.Uuid(), nullable=True),
        sa.Column("commit_sha", sa.String(length=64), nullable=True),
        sa.Column("error", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "state IN ('pending', 'done', 'failed')",
            name="ck_project_starter_pushes_state",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_project_starter_pushes_project_id_projects",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["library_file_id"],
            ["library_files.id"],
            name="fk_project_starter_pushes_library_file_id_library_files",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("project_id"),
    )
    op.create_index(
        "ix_project_starter_pushes_library_file_id",
        "project_starter_pushes",
        ["library_file_id"],
    )


def downgrade() -> None:
    op.drop_table("project_starter_pushes")
