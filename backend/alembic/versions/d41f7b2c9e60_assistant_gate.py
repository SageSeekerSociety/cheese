"""What a person tells their 芝士, and what its gate did

Revision ID: d41f7b2c9e60
Revises: b5d1e9a3c7f2
Create Date: 2026-10-21

A person's 芝士 acts for them where they could (``app.domain.assistant.gate``):
before an action others see, a gate decides whether it simply does it or
turns it into a card the person confirms. ``assistant_rules`` is what the
person tells it about that, one row per person; ``assistant_gate_checks`` is
one row per action judged, with what the judging cost.

Two new, empty tables: nothing to move.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "d41f7b2c9e60"
down_revision: str | Sequence[str] | None = "b5d1e9a3c7f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "assistant_rules",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("ask", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("allow_text", sa.Text(), server_default="", nullable=False),
        sa.Column("ask_text", sa.Text(), server_default="", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["user.id"],
            ondelete="CASCADE",
            name="fk_assistant_rules_user_id_user",
        ),
        sa.PrimaryKeyConstraint("user_id"),
    )
    op.create_table(
        "assistant_gate_checks",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=True),
        sa.Column("work", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("verdict", sa.String(length=16), nullable=False),
        sa.Column("stage", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("cache_read_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("cost_usd", sa.Float(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["user.id"],
            ondelete="CASCADE",
            name="fk_assistant_gate_checks_user_id_user",
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["assistant_conversations.id"],
            ondelete="SET NULL",
            name="fk_assistant_gate_checks_conversation_id",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_assistant_gate_checks_user_id", "assistant_gate_checks", ["user_id", "id"]
    )
    op.create_index(
        "ix_assistant_gate_checks_created_at", "assistant_gate_checks", ["created_at"]
    )
    op.create_index(
        "ix_assistant_gate_checks_conversation_id",
        "assistant_gate_checks",
        ["conversation_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_assistant_gate_checks_conversation_id", table_name="assistant_gate_checks"
    )
    op.drop_index(
        "ix_assistant_gate_checks_created_at", table_name="assistant_gate_checks"
    )
    op.drop_index(
        "ix_assistant_gate_checks_user_id", table_name="assistant_gate_checks"
    )
    op.drop_table("assistant_gate_checks")
    op.drop_table("assistant_rules")
