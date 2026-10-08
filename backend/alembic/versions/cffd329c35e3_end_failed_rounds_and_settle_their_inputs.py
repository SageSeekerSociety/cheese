"""End the rounds a recorded failure left open, and settle the inputs they held

Revision ID: cffd329c35e3
Revises: 7c3e5a9d1f20
Create Date: 2026-10-04

A round the API refuses — a spent budget, a 429 — ends without ever reaching
the completion stamp, so the runner records the terminal outcome itself. #2570
took off the condition that made it skip: it stamped the termination only when
it held no harness background task at that instant, and a session with a
long-running task at the moment it failed lost the stamp for good. New
failures are stamped now; the ones already lost are not.

An input nobody stamped stays unfinished, and ``seat_has_unfinished_input``
reads every unfinished input as an outstanding hold: the seat refuses the next
message, the platform defers it, and no turn runs — a room silent for as long
as it is left alone. Nothing else collects it. The orphan sweep looks at open
*intervals* and leaves a row it cannot judge open on purpose
(``death_evidence.unknown_row``); the earlier migrations (``80aabe850e1e``,
``08b4bcff4ad2``) end intervals, which frees the seat only for an input the
session never took (``unread_input_with_over_work``) — an input the round
*read* is held until a receipt or a termination says otherwise.

So this finishes the two things the failed round would have: it ends the
round's interval, and it writes the terminal stamp on the inputs the round
read.

Which rounds. A round on a room's own line (``task_id IS NULL``), delivered,
started more than six hours ago, with nothing written under its own id for six
hours, carrying a platform event that records it broke: ``kind = 'event'`` with
``event_type`` one of ``turn_failed`` / ``platform_error`` and
``severity = 'error'`` — the predicate of ``ix_blocks_failed_turns``. Six hours
is past the three-hour ceiling every turn is granted, so a round that quiet is
not running. A warning (a timeout, a deploy interruption) is not a failure and
does not count.

What ending means. On the round, ``stopped_at`` — the last thing it wrote, its
Stop having followed within seconds, or ``delivered_at`` if it wrote nothing,
exactly as a landed Stop writes it. On each input the round read
(``execution_work_id`` names the round), ``terminated_at`` and
``termination = 'is_error'``, the columns
``receipts.terminate_work_inputs`` writes, dated when the round ended.
``completed_at`` stays NULL and held blocks stay held: whether an answer was
taken is still unknown, and freeing the seat is not permission to send an
input again.

What this does not touch: a round with no recorded failure, one that wrote
something in the last six hours, one that never reached its session (the orphan
sweep re-sends those), one on a task's line (its task settles it), and any
input the round did not read.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "cffd329c35e3"
down_revision: str | Sequence[str] | None = "7c3e5a9d1f20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: The rounds a recorded failure left behind, and when each ended. Kept as one
#: statement so the selection can be run on its own, read-only, against a
#: deployment before release.
FAILED_ROUNDS = """
SELECT t.id AS turn_id,
       t.topic_id,
       COALESCE(
           (SELECT max(own.created_at) FROM blocks own
             WHERE own.turn_id = t.id),
           t.delivered_at
       ) AS ended_at
  FROM agent_turns t
 WHERE t.task_id IS NULL
   AND t.delivered_at IS NOT NULL
   AND t.started_at < now() - interval '6 hours'
   AND NOT EXISTS (
         SELECT 1 FROM blocks own
          WHERE own.turn_id = t.id
            AND own.created_at >= now() - interval '6 hours')
   AND EXISTS (
         SELECT 1 FROM blocks broke
          WHERE broke.turn_id = t.id
            AND broke.kind = 'event'
            AND (broke.meta ->> 'event_type') IN ('turn_failed', 'platform_error')
            AND (broke.meta ->> 'severity') = 'error')
"""


def upgrade() -> None:
    # The interval first. An input the round never read is freed by the round
    # ending; the terminal stamp below is what frees the ones it did read.
    op.execute(
        f"""
        UPDATE agent_turns
           SET stopped_at = failed.ended_at
          FROM ({FAILED_ROUNDS}) AS failed
         WHERE agent_turns.id = failed.turn_id
           AND agent_turns.stopped_at IS NULL
        """
    )
    op.execute(
        f"""
        UPDATE native_inputs
           SET terminated_at = failed.ended_at,
               termination = 'is_error'
          FROM ({FAILED_ROUNDS}) AS failed
         WHERE native_inputs.execution_work_id = failed.turn_id
           AND native_inputs.topic_id = failed.topic_id
           AND native_inputs.completed_at IS NULL
           AND native_inputs.terminated_at IS NULL
        """
    )


def downgrade() -> None:
    """Downgrade puts nothing back: a re-opened interval makes the room busy with a
    turn nobody is running, and an unstamped input shuts the seat again."""
    pass
