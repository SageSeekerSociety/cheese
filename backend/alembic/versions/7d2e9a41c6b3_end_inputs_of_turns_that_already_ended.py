"""End the inputs of turns that ended before the sweep could

Revision ID: 7d2e9a41c6b3
Revises: 0058abcb379d
Create Date: 2026-10-06

The orphan sweep now ends the inputs a dead turn was holding in the same
transaction that closes the turn. Turns it closed before that change left
their inputs unfinished, and each such input still refuses its seat every
later message: the deliveries behind them retry every 40 seconds and never
dispatch.

This ends those: an input with neither a completion nor a terminal outcome,
whose turn (the one it was sent into, or the one that took it) ended more
than an hour ago. An hour is far past the moment a clean result lands for a
turn that finished normally. Only ``terminated_at`` / ``termination`` are
written, as the sweep writes them: holds stay held and nothing is consumed,
so nothing inside these inputs is sent again.
"""

import sqlalchemy as sa

from alembic import op

revision = "7d2e9a41c6b3"
down_revision = "0058abcb379d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    ended = op.get_bind().execute(
        sa.text(
            """
            UPDATE native_inputs AS n
            SET terminated_at = now(), termination = 'orphaned'
            WHERE n.completed_at IS NULL
              AND n.terminated_at IS NULL
              AND EXISTS (
                SELECT 1 FROM agent_turns AS t
                WHERE t.conversation_id = n.conversation_id
                  AND t.id IN (n.work_id, n.execution_work_id)
                  AND t.stopped_at < now() - interval '1 hour'
              )
            """
        )
    )
    print(f"ended the inputs of turns that already ended: {ended.rowcount}")


def downgrade() -> None:
    # Which rows this wrote is not recorded apart from the sweep's own; an
    # ended input stays ended.
    pass
