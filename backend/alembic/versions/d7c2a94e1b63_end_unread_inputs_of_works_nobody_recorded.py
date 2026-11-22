"""End unread inputs handed to a process nobody recorded, in a work nobody recorded

Revision ID: d7c2a94e1b63
Revises: e63a9b1d4f27

An unread input holds its seat until something ends it: its read, its drop,
the exit of the process it was handed to (``handed_epoch``, b81e4d7c2a95), or
the close of the work it belongs to (``agent_turns``). An input registered
before ``handed_epoch`` existed names no process, so no exit ends it; where its
work has no interval either, no close ends it. Nothing can reach such a row,
and it turns away every later message to its seat. They end here the way a
dead work's inputs end: given up on, with what they held still held. An input
registered within the last day is left alone: its work may be one whose
interval is about to be opened.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "d7c2a94e1b63"
down_revision: str | Sequence[str] | None = "e63a9b1d4f27"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE native_inputs AS input
        SET terminated_at = now(), termination = 'orphaned'
        WHERE input.echoed_at IS NULL
          AND input.terminated_at IS NULL
          AND input.handed_epoch IS NULL
          AND input.registered_at < now() - interval '1 day'
          AND NOT EXISTS (
              SELECT 1 FROM agent_turns AS turn WHERE turn.id = input.work_id
          )
        """
    )


def downgrade() -> None:
    """The ended inputs stay ended: nothing records which rows this ended, and
    the release before reads an ended input as it always has."""
