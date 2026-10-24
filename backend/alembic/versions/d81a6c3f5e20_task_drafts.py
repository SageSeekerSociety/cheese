"""task drafts

Revision ID: d81a6c3f5e20
Revises: c4f2a8e61d37
Create Date: 2026-10-20

A 题目 being written, or the unsaved changes to a published one, kept on the
server (`app.domain.task.drafts`): the publish form survives a reload, and a
person's 芝士 writes into it while it answers, whether or not the page is open.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "d81a6c3f5e20"
down_revision: str | Sequence[str] | None = "c4f2a8e61d37"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The new table's foreign keys lock the tables they point at.
    with_lock_retries('task, space, "user"')
    op.create_table(
        "task_draft",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("space_id", sa.BigInteger(), nullable=False),
        sa.Column("task_id", sa.BigInteger(), nullable=True),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source", sa.String(255), nullable=False, server_default=""),
        sa.Column("fields", postgresql.JSONB(), nullable=False),
        sa.Column("marks", postgresql.JSONB(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["user.id"],
            ondelete="CASCADE",
            name="fk_task_draft_user_id_user",
        ),
        sa.ForeignKeyConstraint(
            ["space_id"],
            ["space.id"],
            ondelete="CASCADE",
            name="fk_task_draft_space_id_space",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["task.id"],
            ondelete="CASCADE",
            name="fk_task_draft_task_id_task",
        ),
    )
    op.create_index(
        "ix_task_draft_user_space",
        "task_draft",
        ["user_id", "space_id", "updated_at"],
    )
    op.create_index("ix_task_draft_space_id", "task_draft", ["space_id"])
    op.create_index("ix_task_draft_task_id", "task_draft", ["task_id"])


def downgrade() -> None:
    with_lock_retries('task, space, "user"')
    op.drop_index("ix_task_draft_task_id", table_name="task_draft")
    op.drop_index("ix_task_draft_space_id", table_name="task_draft")
    op.drop_index("ix_task_draft_user_space", table_name="task_draft")
    op.drop_table("task_draft")
