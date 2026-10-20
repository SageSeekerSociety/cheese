"""drop the landing record

Revision ID: c3f7a1e9d204
Revises: b6e1d47c2a95
Create Date: 2026-10-20

The platform stopped watching a merge on the default branch in the previous
release; `task_landings` stayed as the record that one merge landed on its
task once. `delivery_landed` now recognises that merge by the commit it
delivered (`tasks.delivered_head`), which survives the task going on to its
next step and being reopened, so no row is written or read any more.

The outgoing release still maps the table and writes it on a landing. During
the swap window it can therefore see it gone and fail the landing it is
recording; the merge itself is already on the forge and the next poll lands it
again in the new release. Nothing else reads this table.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries

from alembic import op

revision: str = "c3f7a1e9d204"
down_revision: str | Sequence[str] | None = "b6e1d47c2a95"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with_lock_retries("task_landings, tasks")
    op.drop_table("task_landings")  # migration-safety: allow drop-table — it only records a merge that tasks.delivered_head now records


def downgrade() -> None:
    # The table's shape at b6e1d47c2a95, its watch columns unmapped and
    # `watch_until` nullable. The contents are gone with the code that wrote
    # them; an empty table is what a downgrade can honestly restore.
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
        sa.Column("watch_until", sa.DateTime(timezone=True), nullable=True),
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
