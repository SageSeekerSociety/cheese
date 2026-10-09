"""A read cursor is a block number, not a time

Revision ID: 4e8f1c1e2b22
Revises: 92992f76a580
Create Date: 2026-10-09

Unread was "blocks by others dated after the cursor". A block is dated when it
began, not when it was stored: 芝士's message is stored once the step after it
arrives, dated a moment earlier. Read the room in that moment and the cursor
passes its date before it exists; it is never counted. A cursor that is the
number of the last block stored when the person read (`blocks.seq`) has no such
gap: whatever is stored after it has a larger number.

`last_read_seq` starts at the number of the newest block dated at or before
`last_read_at`, which counts the same blocks as unread that the time did for
every block numbered by `5f1b7d54bffa` (numbered in date order). `last_read_at`
stops being written and becomes nullable here; the running release still reads
and writes it while this deploy runs, so it is dropped in a later release.
A read made on that release in the minutes the deploy takes does not move the
new cursor: that room shows its unread again until it is opened.

The rows are few (1,422 on dev, 2026-10-09) and each reads one entry of
`ix_blocks_conversation_created_at`, so the backfill is one statement. It
recomputes only cursors still at 0, so running it again changes nothing.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries

from alembic import op

revision: str = "4e8f1c1e2b22"
down_revision: str | Sequence[str] | None = "92992f76a580"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with_lock_retries("topic_read_states")
    op.add_column(
        "topic_read_states",
        sa.Column("last_read_seq", sa.BigInteger(), nullable=False, server_default="0"),
    )
    # migration-safety: allow alter-column-existing — dropping NOT NULL reads nothing, and the running release still writes the column
    op.alter_column("topic_read_states", "last_read_at", nullable=True)
    with op.get_context().autocommit_block():
        op.execute("""
            UPDATE topic_read_states r SET last_read_seq = coalesce((
                SELECT b.seq FROM blocks b
                WHERE b.conversation_id = r.topic_id
                  AND b.created_at <= r.last_read_at
                ORDER BY b.created_at DESC, b.id DESC
                LIMIT 1
            ), 0)
            WHERE r.last_read_seq = 0 AND r.last_read_at IS NOT NULL
        """)


def downgrade() -> None:
    with_lock_retries("topic_read_states")
    op.execute(
        "UPDATE topic_read_states SET last_read_at = now() WHERE last_read_at IS NULL"
    )
    op.alter_column("topic_read_states", "last_read_at", nullable=False)
    op.drop_column("topic_read_states", "last_read_seq")
