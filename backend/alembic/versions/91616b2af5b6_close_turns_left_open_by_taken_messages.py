"""End the turns of messages that were answered inside another turn

Revision ID: 91616b2af5b6
Revises: da05dacf50ae
Create Date: 2026-10-03

A message that reached a session while another turn held its seat was read at
that turn's next tool boundary and answered inside it. Its own turn never got a
``stopped_at``: every record the session wrote for it, the result included,
named the running turn. Running is exactly ``stopped_at IS NULL``, so each such
row kept its room busy for good: environment changes refused, the machine
counted as in use, new messages held back as if the agent were mid-turn.

#2502 ends these turns when the turn that read them ends, but only while the
backend that saw the message being read is still running. The rows already
left open have nothing left that will end them, so this ends them.

Which rows. An open turn on a room's own line is ended when all of these hold:

1. It was sent while another turn on the same seat (room and agent) was
   running: that other turn started before it, and either ended after it was
   sent or wrote something into the room after it was sent. A row that merely
   overlaps another *open* row does not count, because an open row may itself
   be one of these leftovers; only a turn seen alive after the send shows the
   session was busy with it.
2. It was delivered. One that never reached the session is the orphan sweep's
   to re-send, not this migration's to end.
3. It started more than six hours before this migration runs, and nothing
   carrying its turn id has been written in the last six hours.

Why it cannot end a turn that is running. A message read inside another turn
never writes anything under its own turn id again. A turn that is really
running is the session's current work, and what it writes (its replies, the
progress notices) carries its own id. So a turn that has written nothing for
six hours is not one a session is working on. Six hours is three times the
orphan sweep's own two-hour line for a turn too old to act on, and far beyond
the liveness gates that end a silent turn. Condition 1 is what limits this to
the rows the bug left: a turn that never had a live neighbour on its seat (one
whose own Stop was lost some other way) is not ended here.

What ending means. Exactly what the runtime writes when a taken message's turn
ends (``_close_open_turns``): ``stopped_at``, nothing else. Its input was
already settled under the turn that read it, which completes the inputs
executed under its own id, and its message block was marked consumed by that
turn. No usage row is written, since none was spent under this id.

When. The moment the turn that read it ended, which is when #2502 would have
ended it: the earliest end, after the message was sent, of a turn on the same
seat that started before it. When that turn has not ended either, the time of
this migration, since nothing records a better one.

Downgrade puts nothing back: a re-opened row would make the room busy again
with a turn nobody is running.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "91616b2af5b6"
down_revision: str | Sequence[str] | None = "da05dacf50ae"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: The rows to end and when each ended. Kept as one statement so the selection
#: can be run on its own, read-only, against a deployment before release.
TAKEN_TURNS = """
SELECT t.id,
       COALESCE(
           (SELECT min(host.stopped_at) FROM agent_turns host
             WHERE host.topic_id = t.topic_id
               AND host.agent_handle = t.agent_handle
               AND host.task_id IS NULL
               AND host.id <> t.id
               AND host.started_at < t.started_at
               AND host.stopped_at > t.started_at),
           now()
       ) AS ended_at
  FROM agent_turns t
 WHERE t.stopped_at IS NULL
   AND t.task_id IS NULL
   AND t.agent_handle IS NOT NULL
   AND t.delivered_at IS NOT NULL
   AND t.started_at < now() - interval '6 hours'
   AND NOT EXISTS (
         SELECT 1 FROM blocks own
          WHERE own.turn_id = t.id
            AND own.created_at >= now() - interval '6 hours')
   AND EXISTS (
         SELECT 1 FROM agent_turns host
          WHERE host.topic_id = t.topic_id
            AND host.agent_handle = t.agent_handle
            AND host.task_id IS NULL
            AND host.id <> t.id
            AND host.started_at < t.started_at
            AND (host.stopped_at > t.started_at
                 OR (host.stopped_at IS NULL
                     AND EXISTS (
                           SELECT 1 FROM blocks said
                            WHERE said.turn_id = host.id
                              AND said.created_at > t.started_at))))
"""


def upgrade() -> None:
    op.execute(
        f"""
        UPDATE agent_turns
           SET stopped_at = taken.ended_at
          FROM ({TAKEN_TURNS}) AS taken
         WHERE agent_turns.id = taken.id
           AND agent_turns.stopped_at IS NULL
        """
    )


def downgrade() -> None:
    pass
