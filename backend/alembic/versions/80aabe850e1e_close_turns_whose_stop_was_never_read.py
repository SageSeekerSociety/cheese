"""End the turns whose Stop the room's reader never got past

Revision ID: 80aabe850e1e
Revises: 91616b2af5b6
Create Date: 2026-10-04

A session on a runner older than the input protocol has its turns settled from
what its journal retained. When that evidence was incomplete (an input echo
without an exact receipt identity), settling raised, and the room's reader
stopped at that turn's result on every poll. The Stop that ends a turn is that
result, so the turn's interval was never closed; and everything the session
wrote after it, including the ends of the turns it ran next, was never read
either. Running is exactly ``stopped_at IS NULL``, so each such row kept its
room busy: environment changes refused, new messages held back.

The reader no longer stops there. The rows already left open are past the
reader's two-hour line for landing a record (``STALE_S``), so it steps over
their Stops instead of landing them, and nothing else will end them. This ends
them.

Which rows. An open, delivered turn on a room's own line that started more
than six hours before this migration runs, and either:

1. Its seat moved on. Nothing carrying its id was written in the last six
   hours, and a later turn on the same seat (room and agent) wrote under its
   own id after this turn's last output. A session answers an input taken into
   the turn it is running under that turn's id, so output under a later turn's
   own id means the session had finished this one.
2. The session started it itself (no prompt: ``resendable`` false and empty
   ``content``), and its author has written nothing in the room in the last
   six hours. Its blocks are written under its author, the agent of the
   session, so six hours with nothing from that author means the session is
   not working on it. Another agent's blocks can carry its id (the room's
   newest open turn is what an agent's message is attached to), which is why
   its own author, not its id, is what is checked.

What this does not end: a turn that is still writing, one sent recently, one
that never reached its session (the orphan sweep re-sends those), and a turn
whose seat shows nothing after it, which is left for a person.

What ending means. Exactly what the runtime writes when a Stop lands
(``_close_open_turns``): ``stopped_at``, nothing else. Its value is the
last thing the turn wrote, which its Stop followed within seconds; a turn that
wrote nothing ends when it was delivered.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "80aabe850e1e"
down_revision: str | Sequence[str] | None = "91616b2af5b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: The rows to end and when each ended. Kept as one statement so the selection
#: can be run on its own, read-only, against a deployment before release.
UNREAD_STOPS = """
SELECT t.id,
       COALESCE(
           (SELECT max(own.created_at) FROM blocks own
             WHERE own.turn_id = t.id
               AND (t.resendable OR t.content <> '' OR own.author = t.author)),
           t.delivered_at
       ) AS ended_at
  FROM agent_turns t
 WHERE t.stopped_at IS NULL
   AND t.task_id IS NULL
   AND t.delivered_at IS NOT NULL
   AND t.started_at < now() - interval '6 hours'
   AND (
         (NOT EXISTS (
                SELECT 1 FROM blocks own
                 WHERE own.turn_id = t.id
                   AND own.created_at >= now() - interval '6 hours')
          AND EXISTS (
                SELECT 1 FROM agent_turns later
                  JOIN blocks said ON said.turn_id = later.id
                 WHERE later.topic_id = t.topic_id
                   AND later.task_id IS NULL
                   AND later.agent_handle IS NOT DISTINCT FROM t.agent_handle
                   AND later.id <> t.id
                   AND later.started_at > t.started_at
                   AND said.created_at > COALESCE(
                         (SELECT max(own.created_at) FROM blocks own
                           WHERE own.turn_id = t.id),
                         t.delivered_at)))
      OR (NOT t.resendable
          AND t.content = ''
          AND NOT EXISTS (
                SELECT 1 FROM blocks said
                 WHERE said.topic_id = t.topic_id
                   AND said.author = t.author
                   AND said.created_at >= now() - interval '6 hours'))
   )
"""


def upgrade() -> None:
    op.execute(
        f"""
        UPDATE agent_turns
           SET stopped_at = unread.ended_at
          FROM ({UNREAD_STOPS}) AS unread
         WHERE agent_turns.id = unread.id
           AND agent_turns.stopped_at IS NULL
        """
    )


def downgrade() -> None:
    """Downgrade puts nothing back: a re-opened row would make the room busy again
    with a turn nobody is running."""
    pass
