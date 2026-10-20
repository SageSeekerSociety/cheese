"""tasks carry their teammate's progress report

Revision ID: b7d3e5a9c214
Revises: b7c1e4d92a10
Create Date: 2026-10-19

Where the AI teammate doing a task says its work stands
(`room_task.models.TaskProgress`), with a short note, when and by whom it said
so, and when its session ended after that. All nullable: a task whose teammate
has said nothing yet has none of them, and existing tasks start there.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries

from alembic import op

revision: str = "b7d3e5a9c214"
down_revision: str | Sequence[str] | None = "b7c1e4d92a10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Copied from `room_task.models.TaskProgress` (migrations do not import `app`).
STATES = ("working", "waiting", "blocked")


def upgrade() -> None:
    with_lock_retries("tasks")
    op.add_column("tasks", sa.Column("progress_state", sa.String(16), nullable=True))
    op.add_column("tasks", sa.Column("progress_note", sa.String(200), nullable=True))
    op.add_column(
        "tasks", sa.Column("progress_set_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("tasks", sa.Column("progress_set_by", sa.String(64), nullable=True))
    op.add_column(
        "tasks", sa.Column("agent_left_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_check_constraint(
        "ck_tasks_progress_state",
        "tasks",
        "progress_state IN ({})".format(", ".join(f"'{s}'" for s in STATES)),
        postgresql_not_valid=True,
    )
    with op.get_context().autocommit_block():
        op.execute("ALTER TABLE tasks VALIDATE CONSTRAINT ck_tasks_progress_state")


def downgrade() -> None:
    with_lock_retries("tasks")
    op.drop_constraint("ck_tasks_progress_state", "tasks", type_="check")
    for column in (
        "agent_left_at",
        "progress_set_by",
        "progress_set_at",
        "progress_note",
        "progress_state",
    ):
        op.drop_column("tasks", column)
