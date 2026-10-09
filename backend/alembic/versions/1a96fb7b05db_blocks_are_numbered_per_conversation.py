"""Every block is numbered in the order it was stored in its conversation

Revision ID: 1a96fb7b05db
Revises: bc82f9d6481a
Create Date: 2026-10-09

A page that lost its connection asks for what it missed. It used to ask by
time: everything after the newest block it holds. But a block's `created_at`
is not when it was stored. 芝士's message is dated when it began and stored
once the step after it arrives, so it lands behind blocks already sent, and a
page that reconnected in between never asked for it. `seq` is the order of
storing: 1, 2, 3 … within each conversation, and "after n" misses nothing.

The number is given by the database, not by the application. Every writer gets
one, the ten or so places that build a `Block` and the release still serving
while this deploy runs alike, which does not know the column exists. Each
insert takes the next number from its conversation's counter
(`conversations.last_seq`), holding that row until it commits, so two writers
in one conversation are numbered in the order they commit and a reader never
sees n + 1 before n.

The counter starts at the number of blocks each conversation already holds;
those are numbered 1 … n by `5f1b7d54bffa` in the order rooms show them, so the
new blocks, numbered from n + 1, come after all of them. Counting under the lock
is what makes n exact: nothing is stored between the count and the trigger. On
dev (2026-10-09, 498,893 blocks in 1,585 conversations) the count took 0.16 s.
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
        sa.Column("last_seq", sa.BigInteger(), nullable=False, server_default="0"),
    )
    op.add_column("blocks", sa.Column("seq", sa.BigInteger(), nullable=True))
    op.execute("""
        UPDATE conversations c SET last_seq = held.n
        FROM (SELECT conversation_id, count(*) AS n FROM blocks GROUP BY 1) held
        WHERE c.id = held.conversation_id
    """)
    op.execute("""
        CREATE FUNCTION block_numbered() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            UPDATE conversations SET last_seq = last_seq + 1
            WHERE id = NEW.conversation_id
            RETURNING last_seq INTO NEW.seq;
            RETURN NEW;
        END;
        $$
    """)
    op.execute("""
        CREATE TRIGGER blocks_numbered
        BEFORE INSERT ON blocks
        FOR EACH ROW EXECUTE FUNCTION block_numbered()
    """)


def downgrade() -> None:
    with_lock_retries("conversations, blocks")
    op.execute("DROP TRIGGER blocks_numbered ON blocks")
    op.execute("DROP FUNCTION block_numbered()")
    op.drop_column("blocks", "seq")
    op.drop_column("conversations", "last_seq")
