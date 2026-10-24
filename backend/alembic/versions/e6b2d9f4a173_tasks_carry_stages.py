"""tasks carry stages

Revision ID: e6b2d9f4a173
Revises: 1f0ec6a6894e
Create Date: 2026-10-24

A 题目's 阶段 (`app.domain.task.stages`): one row per stage, in order, with its
id (stable across reorders), name, what it asks for, and when it is due. Each submission item, each rubric
line and each submission names the stage it belongs to by index. Every
existing row is in stage 0, and an existing task has no stage rows: a task
without stages is one implicit stage, so nothing it does changes.

The three columns are NOT NULL with a server default, so adding them does not
rewrite the tables, and the previous release, which does not map them, writes
rows that land in stage 0.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries

from alembic import op

revision: str = "e6b2d9f4a173"
down_revision: str | Sequence[str] | None = "1f0ec6a6894e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = "task, task_submission_schema, task_rubric_item, task_submission"


def upgrade() -> None:
    # The new table's foreign key locks `task` too.
    with_lock_retries(_TABLES)
    op.create_table(
        "task_stages",
        sa.Column("task_id", sa.BigInteger(), nullable=False),
        sa.Column("index", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(16), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("task_id", "index"),
        sa.UniqueConstraint("task_id", "key", name="uq_task_stages_task_id_key"),
        sa.ForeignKeyConstraint(
            ["task_id"], ["task.id"], name="fk_task_stages_task_id_task"
        ),
    )
    op.add_column(
        "task_submission_schema",
        sa.Column("stage", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "task_rubric_item",
        sa.Column("stage", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "task_submission",
        sa.Column("stage", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    with_lock_retries(_TABLES)
    op.drop_column("task_submission", "stage")
    op.drop_column("task_rubric_item", "stage")
    op.drop_column("task_submission_schema", "stage")
    op.drop_table("task_stages")
