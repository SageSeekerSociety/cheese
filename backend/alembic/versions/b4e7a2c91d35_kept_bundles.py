"""A task's uncommitted work, kept by the platform for a repository not its own

Revision ID: b4e7a2c91d35
Revises: d7c2a94e1b63
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries

from alembic import op

revision: str = "b4e7a2c91d35"
down_revision: str | Sequence[str] | None = "d7c2a94e1b63"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with_lock_retries("tasks")
    op.create_table(
        "kept_bundles",
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("cut_from", sa.String(64), nullable=False),
        sa.Column("commit", sa.String(64), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("saved_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["tasks.id"],
            ondelete="CASCADE",
            name="fk_kept_bundles_task_id_tasks",
        ),
        sa.PrimaryKeyConstraint("task_id"),
    )


def downgrade() -> None:
    op.drop_table("kept_bundles")
