"""What a 芝士 drafted for the person to send

Revision ID: c3e8a1d5f240
Revises: a7c2e4f81b39
Create Date: 2026-10-21

A person's 芝士 drafts a message to a channel, or a task to open in one, and
the person sends it or puts it aside (``app.domain.assistant.proposals``). One
row per draft, with whether it was sent and what came of it.

A new, empty table: nothing to move.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "c3e8a1d5f240"
down_revision: str | Sequence[str] | None = "a7c2e4f81b39"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "assistant_proposals",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("work", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "state", sa.String(length=16), server_default="pending", nullable=False
        ),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["user.id"],
            ondelete="CASCADE",
            name="fk_assistant_proposals_user_id_user",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_assistant_proposals_work", "assistant_proposals", ["work", "id"]
    )
    op.create_index(
        "ix_assistant_proposals_user_id", "assistant_proposals", ["user_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_assistant_proposals_user_id", table_name="assistant_proposals")
    op.drop_index("ix_assistant_proposals_work", table_name="assistant_proposals")
    op.drop_table("assistant_proposals")
