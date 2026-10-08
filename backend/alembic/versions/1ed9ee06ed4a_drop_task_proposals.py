"""AI teammates create tasks; nothing records a proposal or a message's one task

Revision ID: 1ed9ee06ed4a
Revises: c3a8e5f1d702
Create Date: 2026-10-07

c3a8e5f1d702 (#3059) moved every proposal's task under the message it came
from and stopped reading both `task_proposals` and `blocks.upgraded_to_task_id`:
a teammate creates the task itself, and a message holds any number of tasks
through `tasks.upgraded_from_block_id`. Both stayed one deploy longer because
the previous image still read them while migrations ran. That image is gone.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries

from alembic import op

revision: str = "1ed9ee06ed4a"
down_revision: str | Sequence[str] | None = "c3a8e5f1d702"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with_lock_retries("blocks, task_proposals")
    op.drop_column("blocks", "upgraded_to_task_id")
    op.drop_table("task_proposals")


def downgrade() -> None:
    op.add_column("blocks", sa.Column("upgraded_to_task_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_blocks_upgraded_to_task_id",
        "blocks",
        "tasks",
        ["upgraded_to_task_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_table(
        "task_proposals",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "project_id",
            sa.Uuid(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "room_id",
            sa.Uuid(),
            sa.ForeignKey("topics.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "conversation_id",
            sa.Uuid(),
            sa.ForeignKey(
                "conversations.id",
                ondelete="CASCADE",
                name="fk_task_proposals_conversation_id",
            ),
            nullable=False,
        ),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("proposed_by", sa.String(64), nullable=False),
        sa.Column("state", sa.String(16), nullable=False, server_default="open"),
        sa.Column(
            "task_id",
            sa.Uuid(),
            sa.ForeignKey("tasks.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("decided_by", sa.String(64), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_task_proposals_project_id", "task_proposals", ["project_id"])
    op.create_index("ix_task_proposals_room_id", "task_proposals", ["room_id"])
    op.create_index(
        "ix_task_proposals_conversation_id", "task_proposals", ["conversation_id"]
    )
