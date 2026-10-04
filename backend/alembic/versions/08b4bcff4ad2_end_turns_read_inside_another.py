"""End the turns whose input was read inside another turn that has ended

Revision ID: 08b4bcff4ad2
Revises: 761d32f96d84
Create Date: 2026-10-04

A message that reaches a session while it is in the middle of a turn is read at
that turn's next tool boundary and answered inside it. It opens no turn of its
own, so it ends when the turn that read it ends. Ending it used to depend on the
memory of the backend that saw the session read it, and dev replaces its
backend on every merge: when that backend was replaced before the reading turn
ended, or the input was sent seconds after a new backend started, the input's
turn was never ended. Running is exactly ``stopped_at IS NULL``, so each such
row kept its room busy: environment changes refused, new messages held back.

The room now ends those turns from the delivery ledger, which every backend
reads. The rows already left open are ended here, from the same ledger.

Which rows: an open, delivered turn whose input the ledger records as echoed
under another work (``native_inputs.execution_work_id``), when that work has
ended. The echo is the session's own statement that it read the input inside
that work, and an input read there never opens a turn of its own.

What ending means: ``stopped_at``, as the runtime writes when a Stop lands, at
the moment the work that read it ended.

Downgrade puts nothing back: a re-opened row would make its room busy again
with a turn nobody is running.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "08b4bcff4ad2"
down_revision: str | Sequence[str] | None = "761d32f96d84"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: The rows to end and when each ended. One statement, so the selection can be
#: run on its own, read-only, against a deployment before release.
READ_INSIDE_ANOTHER = """
SELECT DISTINCT ON (t.id) t.id, reader.stopped_at AS ended_at
  FROM agent_turns t
  JOIN native_inputs n ON n.work_id = t.id AND n.topic_id = t.topic_id
  JOIN agent_turns reader ON reader.id = n.execution_work_id
 WHERE t.stopped_at IS NULL
   AND t.delivered_at IS NOT NULL
   AND n.execution_work_id <> t.id
   AND n.echoed_at IS NOT NULL
   AND reader.stopped_at IS NOT NULL
 ORDER BY t.id, reader.stopped_at
"""


def upgrade() -> None:
    op.execute(
        f"""
        UPDATE agent_turns
           SET stopped_at = read.ended_at
          FROM ({READ_INSIDE_ANOTHER}) AS read
         WHERE agent_turns.id = read.id
           AND agent_turns.stopped_at IS NULL
        """
    )


def downgrade() -> None:
    pass
