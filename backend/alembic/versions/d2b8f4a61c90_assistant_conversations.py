"""Conversations with a person's 芝士 outside any project

Revision ID: d2b8f4a61c90
Revises: c4d7e2a9f158
Create Date: 2026-10-01 06:00:00

Two new tables, nothing existing is touched: what the person and 芝士 said,
in order (``assistant_messages``), and per conversation the model's own copy of
it (``assistant_conversations.history``), which is cut back and summarised as
it grows.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "d2b8f4a61c90"
down_revision: str | Sequence[str] | None = "c4d7e2a9f158"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "assistant_conversations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("user.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("place_kind", sa.String(16), nullable=False),
        sa.Column("place_id", sa.String(64), nullable=False, server_default=""),
        sa.Column("title", sa.String(120), nullable=False, server_default=""),
        sa.Column(
            "last_active_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "history",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_assistant_conversations_owner_place",
        "assistant_conversations",
        ["user_id", "place_kind", "place_id", "last_active_at"],
    )
    op.create_table(
        "assistant_messages",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "conversation_id",
            sa.Uuid(),
            sa.ForeignKey("assistant_conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("text", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_assistant_messages_conversation_seq",
        "assistant_messages",
        ["conversation_id", "seq"],
    )


def downgrade() -> None:
    op.drop_table("assistant_messages")
    op.drop_table("assistant_conversations")
