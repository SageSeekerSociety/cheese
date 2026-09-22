"""teaching units — the course timeline

A course is a sequence of 教学单元. Each row is one week (or one class) of a
题目板: what it teaches, what it hands out, what is due. ``published_at`` NULL
means the unit does not exist for a student yet — that is the whole point of the
column, so it is deliberately nullable and unindexed.

Revision ID: 93ac2aa4b5fd
Revises: 16f4e85e5971
Create Date: 2026-09-22 12:10:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "93ac2aa4b5fd"
down_revision: str | Sequence[str] | None = "16f4e85e5971"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(sa.schema.CreateSequence(sa.Sequence("teaching_unit_seq")))

    op.create_table(
        "teaching_unit",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Sequence("teaching_unit_seq"),
            nullable=False,
        ),
        sa.Column("space_id", sa.BigInteger(), nullable=False),
        sa.Column("week", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("knowledge_point_ids", postgresql.JSONB(), nullable=False),
        sa.Column("material_ids", postgresql.JSONB(), nullable=False),
        sa.Column("assignment_task_id", sa.BigInteger(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    # One board's timeline, in course order — the only query there is.
    op.create_index(
        "ix_teaching_unit_space_week", "teaching_unit", ["space_id", "week"]
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_teaching_unit_space_week", table_name="teaching_unit")
    op.drop_table("teaching_unit")
    op.execute(sa.schema.DropSequence(sa.Sequence("teaching_unit_seq")))
