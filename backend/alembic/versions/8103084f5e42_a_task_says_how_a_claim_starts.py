"""A task says how a claim of it starts

``task.start_mode``: ``DIRECT`` (the claim is approved the moment it is made,
its project opens then, and everyone hands in by the task's own deadline) or
``APPLY`` (the claim waits for the publisher; its project opens and its
deadline of ``default_deadline`` days starts when it is approved).

Every existing task becomes ``APPLY``. Since #4810 every claim of every task
has been created pending and waited for the publisher, with its deadline set
at approval, and there has been no setting to start directly; ``APPLY`` is that
behaviour. The release still serving does not map the column, so the rows it
inserts take the default and behave as they do under it.

Revision ID: 8103084f5e42
Revises: e5a17c93d2b4
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries

from alembic import op

revision: str = "8103084f5e42"
down_revision: str | Sequence[str] | None = "e5a17c93d2b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with_lock_retries("task")
    op.add_column(
        "task",
        sa.Column("start_mode", sa.String(16), nullable=False, server_default="APPLY"),
    )
    op.create_check_constraint(
        "ck_task_start_mode",
        "task",
        "start_mode IN ('DIRECT', 'APPLY')",
        postgresql_not_valid=True,
    )
    with op.get_context().autocommit_block():
        op.execute("ALTER TABLE task VALIDATE CONSTRAINT ck_task_start_mode")


def downgrade() -> None:
    op.drop_constraint("ck_task_start_mode", "task", type_="check")
    op.drop_column("task", "start_mode")
