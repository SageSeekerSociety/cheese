"""One culprit search per red main batch, kept between sweeps.

A probe reruns the failed tests on one commit in CI; the plan is put down
between probes, so the row holds the plan and the run it is waiting on.

Revision ID: 8f2c5d1a9b47
Revises: d6a4c8f0192b
Create Date: 2026-10-19 09:20:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8f2c5d1a9b47"
down_revision: str | Sequence[str] | None = "d6a4c8f0192b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "culprit_searches",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("head", sa.String(length=64), nullable=False),
        sa.Column("tests", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("state", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=True),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            ondelete="CASCADE",
            name="fk_culprit_searches_project_id_projects",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "project_id", "head", name="uq_culprit_searches_project_id_head"
        ),
    )
    op.create_index(
        op.f("ix_culprit_searches_project_id"),
        "culprit_searches",
        ["project_id"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_culprit_searches_project_id"), table_name="culprit_searches")
    op.drop_table("culprit_searches")
