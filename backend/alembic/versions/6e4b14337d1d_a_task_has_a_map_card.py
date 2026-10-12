"""a task has a card for the project map

Revision ID: 6e4b14337d1d
Revises: f175b976a14a

`task_map_cards` holds one row per task: what it is, where it has got to, who it
waits on, the terms a reader may not know, and who changed it last and when
(`app.domain.task_map`). A task without a row has no card yet. The table is new,
so nothing serving reads it; the foreign key to `tasks` takes a lock there,
taken up front.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "6e4b14337d1d"
down_revision: str | Sequence[str] | None = "f175b976a14a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with_lock_retries("tasks")
    op.create_table(
        "task_map_cards",
        sa.Column(
            "task_id",
            sa.Uuid(),
            sa.ForeignKey(
                "tasks.id", ondelete="CASCADE", name="fk_task_map_cards_task_id_tasks"
            ),
            primary_key=True,
        ),
        sa.Column("what", sa.Text(), nullable=False, server_default=""),
        sa.Column("progress", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "waiting_on", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        sa.Column(
            "terms", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        sa.Column("updated_by", sa.String(64), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("task_map_cards")
