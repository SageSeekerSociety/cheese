"""Number the blocks stored before conversations counted them

Revision ID: 5f1b7d54bffa
Revises: 0dd66b328211
Create Date: 2026-10-09

`0dd66b328211` numbers every block stored from then on above the number of
blocks its conversation already held (`seq_floor`). This gives those blocks
1 … n, in the order rooms show them (`created_at`, then `id`), so they sit below
every number handed out since.

One conversation at a time, each its own statement and its own commit: a
conversation is numbered whole or not at all, so a run that stops halfway
starts again from the conversations still holding unnumbered blocks and numbers
them the same way. Every block from before the counter is unnumbered and every
block after it has a number, so "unnumbered" is exactly what is left to do.
The largest conversation on dev held 44,746 blocks (2026-10-09); the rest of
the table stays free to read and write while each one is numbered.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "5f1b7d54bffa"
down_revision: str | Sequence[str] | None = "0dd66b328211"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NUMBER_ONE = sa.text("""
    UPDATE blocks b SET seq = numbered.n
    FROM (
        SELECT id, row_number() OVER (ORDER BY created_at, id) AS n
        FROM blocks
        WHERE conversation_id = :conversation AND seq IS NULL
    ) numbered
    WHERE b.id = numbered.id
""")


def upgrade() -> None:
    with op.get_context().autocommit_block():
        bind = op.get_bind()
        waiting = bind.execute(
            sa.text("SELECT DISTINCT conversation_id FROM blocks WHERE seq IS NULL")
        ).scalars()
        for conversation in list(waiting):
            bind.execute(NUMBER_ONE, {"conversation": conversation})


def downgrade() -> None:
    """Nothing to undo: the numbers go with the column, in `1a96fb7b05db`'s
    downgrade."""
