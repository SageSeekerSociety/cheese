"""project_situations: the 「现状」 written at the top of a project's overview

Revision ID: 7c3e5a1f9b24
Revises: 5d0e8b3c7a21
Create Date: 2026-10-16

One row per project, written the first time someone opens its overview. A new
table, so no backfill: a project gets its first one when it is next opened.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries

from alembic import op

revision: str = "7c3e5a1f9b24"
down_revision: str | Sequence[str] | None = "b34ba429e24d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The foreign key takes a lock on projects, which the live backend writes.
    with_lock_retries("projects")
    op.create_table(
        "project_situations",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("content", sa.Text(), server_default="", nullable=False),
        sa.Column("written_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("major", sa.String(200), server_default="", nullable=False),
        sa.Column("minor", sa.String(200), server_default="", nullable=False),
        sa.Column("writing_since", sa.DateTime(timezone=True), nullable=True),
        sa.Column("tried_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("model", sa.String(128), server_default="", nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            ondelete="CASCADE",
            name="fk_project_situations_project_id_projects",
        ),
        sa.PrimaryKeyConstraint("project_id"),
    )


def downgrade() -> None:
    op.drop_table("project_situations")
