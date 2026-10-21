"""A conversation has chapters

Revision ID: b7e4c9a21d68
Revises: c3e8a1d5f240
Create Date: 2026-10-21

A message someone marked as the start of a section, per conversation
(``app.domain.chapter``). New, empty table: nothing to move.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b7e4c9a21d68"
down_revision: str | Sequence[str] | None = "53f2f3deeb83"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "chapters",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("block_id", sa.Uuid(), nullable=False),
        sa.Column("pinned_by", sa.String(length=128), nullable=False),
        sa.Column("pinned_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["block_id"],
            ["blocks.id"],
            ondelete="CASCADE",
            name="fk_chapters_block_id_blocks",
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            ondelete="CASCADE",
            name="fk_chapters_conversation_id_conversations",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("block_id", name="uq_chapters_block_id"),
    )
    op.create_index("ix_chapters_conversation_id", "chapters", ["conversation_id"])


def downgrade() -> None:
    op.drop_index("ix_chapters_conversation_id", table_name="chapters")
    op.drop_table("chapters")
