"""Drop culprit_searches

Revision ID: 4f905c315e03
Revises: d5706889f78b
Create Date: 2026-10-20

#4448 removed the search for the commit that broke a test and unmapped its
table; that release is deployed. Its foreign key points at `projects`, so the
drop takes that lock too.

The downgrade creates the table again, empty.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "4f905c315e03"
down_revision: str | Sequence[str] | None = "d5706889f78b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with_lock_retries("culprit_searches, projects")
    op.drop_table("culprit_searches")


def downgrade() -> None:
    op.create_table(
        "culprit_searches",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("head", sa.String(64), nullable=False),
        sa.Column("tests", postgresql.JSONB(), nullable=False),
        sa.Column("state", postgresql.JSONB(), nullable=False),
        sa.Column("run_id", sa.String(64), nullable=True),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="culprit_searches_pkey"),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            ondelete="CASCADE",
            name="fk_culprit_searches_project_id_projects",
        ),
        sa.UniqueConstraint(
            "project_id", "head", name="uq_culprit_searches_project_id_head"
        ),
    )
    op.create_index(
        "ix_culprit_searches_project_id", "culprit_searches", ["project_id"]
    )
