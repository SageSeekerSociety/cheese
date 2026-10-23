"""Drop task_landings

Revision ID: 2fe2a1b8a751
Revises: 4f905c315e03
Create Date: 2026-10-20

#4448 stopped watching a task's merge through to its deploy, and #4469 told a
merge that already landed by the task's delivered head instead of this table,
unmapping it; #4482 took the drop back out so that it would follow a deployed
release. That release is deployed. Its foreign key points at `tasks`, so the
drop takes that lock too.

The downgrade creates the table again, empty.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries

from alembic import op

revision: str = "2fe2a1b8a751"
down_revision: str | Sequence[str] | None = "4f905c315e03"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with_lock_retries("task_landings, tasks")
    op.drop_table("task_landings")


def downgrade() -> None:
    op.create_table(
        "task_landings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("pr_number", sa.Integer(), nullable=False),
        sa.Column("merge_sha", sa.String(64), nullable=True),
        sa.Column("landed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("watch_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("checks", sa.String(16), server_default="watching", nullable=False),
        sa.Column("deploy", sa.String(16), server_default="watching", nullable=False),
        sa.Column("environment", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="task_landings_pkey"),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["tasks.id"],
            ondelete="CASCADE",
            name="fk_task_landings_task_id_tasks",
        ),
        sa.CheckConstraint(
            "checks IN ('watching', 'passed', 'failed', 'preexisting',"
            " 'timeout', 'unavailable')",
            name="ck_task_landings_checks",
        ),
        sa.CheckConstraint(
            "deploy IN ('watching', 'deployed', 'failed', 'timeout', 'unavailable')",
            name="ck_task_landings_deploy",
        ),
    )
    op.create_index("ix_task_landings_task_id", "task_landings", ["task_id"])
    op.create_index("ix_task_landings_watch_until", "task_landings", ["watch_until"])
