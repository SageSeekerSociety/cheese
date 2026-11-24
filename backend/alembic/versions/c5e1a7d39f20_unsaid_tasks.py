"""A task opened to edit a project file, not said or listed until it changes

Revision ID: c5e1a7d39f20
Revises: d41c8ea70f93
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries

from alembic import op

revision: str = "c5e1a7d39f20"
down_revision: str | Sequence[str] | None = "d41c8ea70f93"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with_lock_retries("tasks")
    op.create_table(
        "unsaid_tasks",
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["tasks.id"],
            ondelete="CASCADE",
            name="fk_unsaid_tasks_task_id_tasks",
        ),
        sa.PrimaryKeyConstraint("task_id"),
    )


def downgrade() -> None:
    op.drop_table("unsaid_tasks")
