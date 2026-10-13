"""Every block gets a number in the order it was stored in its conversation

Revision ID: 1a96fb7b05db
Revises: bc82f9d6481a
Create Date: 2026-10-09

A page that lost its connection asks for what it missed. It used to ask by
time: everything after the newest block it holds. But a block's `created_at`
is not when it was stored. 芝士's message is dated when it began and stored
once the step after it arrives, so it lands behind blocks already sent, and a
page that reconnected in between never asked for it. `seq` is the order of
storing: 1, 2, 3 … within each conversation, and "after n" misses nothing.

This adds the columns only, which takes no time under the lock: `blocks.seq`,
nullable until every block has one (`92992f76a580`), and
`conversations.seq_floor`, the numbers kept for the blocks a conversation held
before numbering began (`0dd66b328211`).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries

from alembic import op

revision: str = "1a96fb7b05db"
down_revision: str | Sequence[str] | None = "bc82f9d6481a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with_lock_retries("conversations, blocks")
    op.add_column(
        "conversations",
        sa.Column("seq_floor", sa.BigInteger(), nullable=False, server_default="0"),
    )
    op.add_column("blocks", sa.Column("seq", sa.BigInteger(), nullable=True))


def downgrade() -> None:
    with_lock_retries("conversations, blocks")
    op.drop_column("blocks", "seq")
    op.drop_column("conversations", "seq_floor")
