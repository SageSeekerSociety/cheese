"""Blocks no longer link to a room they became

Revision ID: f17973b3f7b6
Revises: d63cc17dd634
Create Date: 2026-10-09

A message once became a room of its own and kept a link to it. Turning a
message into something now makes a task, which points back with
`upgraded_from_block_id`; nothing wrote `blocks.upgraded_to_topic_id` since,
and it was empty on dev. #3191 stopped mapping it and has been deployed, so the
running release no longer selects it: the column goes, and its foreign key and
partial index go with it.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries

from alembic import op

revision: str = "f17973b3f7b6"
down_revision: str | Sequence[str] | None = "d63cc17dd634"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with_lock_retries("blocks, topics")
    op.drop_column("blocks", "upgraded_to_topic_id")


def downgrade() -> None:
    op.add_column(
        "blocks",
        sa.Column(
            "upgraded_to_topic_id",
            sa.Uuid(),
            sa.ForeignKey("topics.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_blocks_upgraded_to_topic_id",
        "blocks",
        ["upgraded_to_topic_id"],
        postgresql_where=sa.text("upgraded_to_topic_id IS NOT NULL"),
    )
