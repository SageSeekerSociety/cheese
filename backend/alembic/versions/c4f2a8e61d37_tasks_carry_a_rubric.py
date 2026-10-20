"""tasks carry a rubric

Revision ID: c4f2a8e61d37
Revises: b7d3e5a9c214
Create Date: 2026-10-20

A 题目's 评分要点 (`app.domain.task.rubric`): one row per line, in order, with
what is scored, its points and what earns them. `task.rubric_public` says
whether the students see it; the graders always do. Existing tasks have no
rubric and keep the default, public, which shows nothing until one is written.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries

from alembic import op

revision: str = "c4f2a8e61d37"
down_revision: str | Sequence[str] | None = "b7d3e5a9c214"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The new table's foreign key locks `task` too.
    with_lock_retries("task")
    op.create_table(
        "task_rubric_item",
        sa.Column("task_id", sa.BigInteger(), nullable=False),
        sa.Column("index", sa.Integer(), nullable=False),
        sa.Column("item", sa.String(64), nullable=False),
        sa.Column("points", sa.Integer(), nullable=False),
        sa.Column("criteria", sa.Text(), nullable=False, server_default=""),
        sa.PrimaryKeyConstraint("task_id", "index"),
        sa.ForeignKeyConstraint(
            ["task_id"], ["task.id"], name="fk_task_rubric_item_task_id_task"
        ),
    )
    op.add_column(
        "task",
        sa.Column(
            "rubric_public", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
    )


def downgrade() -> None:
    with_lock_retries("task")
    op.drop_column("task", "rubric_public")
    op.drop_table("task_rubric_item")
