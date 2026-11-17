"""message_reads: who has read which messages of a conversation

Revision ID: e8a7c5417435
Revises: a3e5c7d9f1b2
Create Date: 2026-11-17

Read receipts. One row per (conversation, reader) holding the stretches of
block numbers (`blocks.seq`) the reader has read, as one ``int8multirange``:
a reader who read a conversation from end to end is one range however long it
is, where a row per reader per message would grow with every message times
every member.

It also takes the first of two steps out of ``native_inputs.seen_block_ids`` /
``seen_by``. They recorded which message an AI teammate's input carried so the
platform could put a 👀 on it; that reaction is gone (#5287), and what an AI
teammate has read is now a row here. The code no longer maps either column, so
``seen_block_ids`` — NOT NULL without a default — must take NULL before the
release that stops writing it runs. Dropping both columns is the second step,
in a release after this one is deployed (`.claude/rules/migrations.md` rule 4).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "e8a7c5417435"
down_revision: str | Sequence[str] | None = "a3e5c7d9f1b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The new foreign key points at ``conversations``, and ``native_inputs`` is
    # altered: both are written by the live backend.
    with_lock_retries("conversations, native_inputs")
    op.create_table(
        "message_reads",
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("reader", sa.String(length=64), nullable=False),
        sa.Column("seqs", postgresql.INT8MULTIRANGE(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name="fk_message_reads_conversation_id_conversations",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("conversation_id", "reader", name="pk_message_reads"),
    )
    op.alter_column(
        "native_inputs",
        "seen_block_ids",
        existing_type=postgresql.JSONB(),
        nullable=True,
    )


def downgrade() -> None:
    op.execute(
        "UPDATE native_inputs SET seen_block_ids = '[]' WHERE seen_block_ids IS NULL"
    )
    op.alter_column(
        "native_inputs",
        "seen_block_ids",
        existing_type=postgresql.JSONB(),
        nullable=False,
    )
    op.drop_table("message_reads")
