"""add ai chat tables

Revision ID: add_ai_chat_001
Revises: 1c64f1712118
Create Date: 2026-01-02
"""

from alembic import op
import sqlalchemy as sa


revision = "add_ai_chat_001"
down_revision = "1c64f1712118"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_conversation",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(255), nullable=False, server_default="New Conversation"),
        sa.Column("model_id", sa.String(100), nullable=False, server_default="gpt-4o-mini"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ai_conversation_user_id", "ai_conversation", ["user_id"])

    op.create_table(
        "ai_message",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("conversation_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("tokens_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["conversation_id"], ["ai_conversation.id"]),
    )
    op.create_index("ix_ai_message_conversation_id", "ai_message", ["conversation_id"])


def downgrade() -> None:
    op.drop_index("ix_ai_message_conversation_id", table_name="ai_message")
    op.drop_table("ai_message")
    op.drop_index("ix_ai_conversation_user_id", table_name="ai_conversation")
    op.drop_table("ai_conversation")
