"""A task remembers the message it came from

Revision ID: a6c3e9d2f417
Revises: c4e9a2f7d153
Create Date: 2026-10-12

`tasks.upgraded_from_block_id` is the main-line message a task shows under. A
task turned from a reply in a 支线 keeps only that 支线's opening message there,
and one an AI teammate creates in the main line or inside another task keeps
nothing, so its page could not say where it came from.
`tasks.source_block_id` is the message itself: the one turned into the task,
or the person's message the teammate's turn was answering. Existing tasks keep
it empty; their page falls back to `upgraded_from_block_id`.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries

from alembic import op

revision: str = "a6c3e9d2f417"
down_revision: str | Sequence[str] | None = "c4e9a2f7d153"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CONSTRAINT = "fk_tasks_source_block_id_blocks"
_INDEX = "ix_tasks_source_block_id"


def upgrade() -> None:
    # The new foreign key locks `blocks` as well as `tasks`.
    with_lock_retries("tasks, blocks")
    op.add_column("tasks", sa.Column("source_block_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        _CONSTRAINT,
        "tasks",
        "blocks",
        ["source_block_id"],
        ["id"],
        ondelete="SET NULL",
        postgresql_not_valid=True,
    )
    with op.get_context().autocommit_block():
        op.execute(f"ALTER TABLE tasks VALIDATE CONSTRAINT {_CONSTRAINT}")
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {_INDEX}")
        op.create_index(
            _INDEX,
            "tasks",
            ["source_block_id"],
            postgresql_concurrently=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(
            _INDEX,
            table_name="tasks",
            postgresql_concurrently=True,
            if_exists=True,
        )
    op.drop_constraint(_CONSTRAINT, "tasks", type_="foreignkey")
    op.drop_column("tasks", "source_block_id")
