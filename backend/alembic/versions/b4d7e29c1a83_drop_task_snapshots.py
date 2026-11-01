"""Drop the task snapshot table

Snapshots of uncommitted task files were stored here and in the private
bucket. The sandbox CLI no longer uploads them (#5071), and the routes that
read them are gone, so the table has no reader or writer.

Revision ID: b4d7e29c1a83
Revises: 4ce146cd5ebb
Create Date: 2026-11-01
"""

import sqlalchemy as sa
from migration_helpers import with_lock_retries

from alembic import op

revision = "b4d7e29c1a83"
down_revision = "4ce146cd5ebb"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The table holds a foreign key to tasks; both are locked before the drop.
    with_lock_retries("task_snapshots, tasks")
    op.drop_table(
        "task_snapshots"
    )  # migration-safety: allow drop-table — #5071 stopped every uploader and restorer; the owner chose to discard the leftovers, so no client calls the previous release's routes.


def downgrade() -> None:
    # The table as a1286f09c001 built it.
    op.create_table(
        "task_snapshots",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "task_id",
            sa.Uuid(),
            sa.ForeignKey("tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("head_sha", sa.String(64), nullable=False),
        sa.Column("snapshot_sha", sa.String(64), nullable=False),
        sa.Column("digest", sa.String(64), nullable=False),
        sa.Column("storage_key", sa.String(1024), nullable=False),
    )
    op.create_index("ix_task_snapshots_task_id", "task_snapshots", ["task_id"])
