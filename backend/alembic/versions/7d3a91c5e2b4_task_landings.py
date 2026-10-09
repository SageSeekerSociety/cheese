"""tasks close after their summary turn; what a merge did on the default branch

Revision ID: 7d3a91c5e2b4
Revises: f17973b3f7b6

A task whose last delivery merged stays open while its AI teammate writes it
up, and closes when that turn ends: `tasks.closing_since` says it is in that
window. `task_landings` holds one row per merged PR of a task: the commit the
merge put on the default branch, and what the platform saw of its checks and
of a deployment that included it.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries

from alembic import op

revision: str = "7d3a91c5e2b4"
down_revision: str | Sequence[str] | None = "f17973b3f7b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with_lock_retries("tasks")
    op.add_column(
        "tasks",
        sa.Column("closing_since", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "task_landings",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "task_id",
            sa.Uuid(),
            sa.ForeignKey(
                "tasks.id", ondelete="CASCADE", name="fk_task_landings_task_id_tasks"
            ),
            nullable=False,
        ),
        sa.Column("pr_number", sa.Integer(), nullable=False),
        sa.Column("merge_sha", sa.String(64), nullable=True),
        sa.Column("landed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("watch_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("checks", sa.String(16), nullable=False, server_default="watching"),
        sa.Column("deploy", sa.String(16), nullable=False, server_default="watching"),
        sa.Column("environment", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "checks IN ('watching', 'passed', 'failed', 'preexisting', 'timeout',"
            " 'unavailable')",
            name="ck_task_landings_checks",
        ),
        sa.CheckConstraint(
            "deploy IN ('watching', 'deployed', 'failed', 'timeout', 'unavailable')",
            name="ck_task_landings_deploy",
        ),
    )
    op.create_index("ix_task_landings_task_id", "task_landings", ["task_id"])
    op.create_index("ix_task_landings_watch_until", "task_landings", ["watch_until"])


def downgrade() -> None:
    op.drop_index("ix_task_landings_watch_until", table_name="task_landings")
    op.drop_index("ix_task_landings_task_id", table_name="task_landings")
    op.drop_table("task_landings")
    op.drop_column("tasks", "closing_since")
