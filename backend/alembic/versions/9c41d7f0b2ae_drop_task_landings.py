"""drop the landing watch's table

Revision ID: 9c41d7f0b2ae
Revises: f4b1298ca631
Create Date: 2026-10-20

The platform no longer watches a task's merge on the default branch: checks
that fail there and deployments that include it are the deployment on-call
line's to notice, not the task's. `task_landings` held one row per merged PR
of a task with what the watch had seen of its checks and deployment; nothing
maps or reads it now.

The table is written only by the watch, which this release removes. During the
swap window the outgoing release can still find it gone and fail a tick of
that watch; that tick reports nothing and the line it fed is being retired in
the same release. No other query reads this table.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries

from alembic import op

revision: str = "9c41d7f0b2ae"
down_revision: str | Sequence[str] | None = "f4b1298ca631"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with_lock_retries("task_landings, tasks")
    op.drop_table("task_landings")  # migration-safety: allow drop-table — only the removed landing watch wrote or read it


def downgrade() -> None:
    # Gives back the table's shape only. What the watch had seen is gone with
    # the watch; an empty table is what a downgrade can honestly restore, and
    # migration tests downgrade through head to reach older revisions.
    with_lock_retries("tasks")
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
