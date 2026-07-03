"""block reactions table (Slack-style emoji reactions)

One row per (block, emoji, author); reacting again with the same emoji removes
the row (toggle). Powers both human reactions and the platform's deterministic
✅ receipt 芝士 puts on the message that summoned it.

Revision ID: c3a8f5d19e42
Revises: a4c9e2b17d05
Create Date: 2026-07-03 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c3a8f5d19e42"
down_revision: Union[str, Sequence[str], None] = "a4c9e2b17d05"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "block_reactions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("block_id", sa.Uuid(), nullable=False),
        sa.Column("emoji", sa.String(length=32), nullable=False),
        sa.Column("author", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["block_id"], ["blocks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("block_id", "emoji", "author", name="uq_block_reaction"),
    )
    op.create_index(
        op.f("ix_block_reactions_block_id"),
        "block_reactions",
        ["block_id"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_block_reactions_block_id"), table_name="block_reactions")
    op.drop_table("block_reactions")
