"""Sessions that point at another teammate's runner stop pointing at it

Revision ID: 7e4b9d2c1a60
Revises: c4f1d8a2e9b7

A turn nobody addressed used to run as the project's default agent while it
acted under the room's seat, which in a room that does not seat the default is
another teammate's (#2129). Such a turn recorded a session row for the default
agent whose `runtime_location` names that teammate's runner. Recovery then
attached two readers to one runner's mirror file, and the room logged
`database is locked`.

This clears `runtime_location` on a row whose recorded runner acts as a seat
that is not its own agent's, where another row in the same room points at the
same runner: that other row is the runner's owner. Only the location is
cleared, so recovery no longer finds the row; its work lease is kept, because a
lease can hold a cloud machine that the platform still has to release.
"""

import sqlalchemy as sa

from alembic import op

revision = "7e4b9d2c1a60"
down_revision = "c4f1d8a2e9b7"
branch_labels = None
depends_on = None

#: A saved agent's seat is `cheese-` and the first 12 hex digits of its id
#: (`app.domain.identity.handles.agent_instance_handle`).
DROP_BORROWED_LOCATIONS = """
UPDATE agent_sessions AS s
   SET runtime_location = NULL
  FROM topics AS t
 WHERE t.id = s.topic_id
   AND s.runtime_location IS NOT NULL
   AND s.runtime_location::jsonb -> 'runtime' ->> 'agent_handle' IS NOT NULL
   AND NOT EXISTS (
       SELECT 1
         FROM agent_instances AS i
        WHERE i.project_id = t.project_id
          AND i.handle = s.agent_handle
          AND 'cheese-' || left(replace(i.id::text, '-', ''), 12)
              = s.runtime_location::jsonb -> 'runtime' ->> 'agent_handle')
   AND EXISTS (
       SELECT 1
         FROM agent_sessions AS o
        WHERE o.topic_id = s.topic_id
          AND o.id <> s.id
          AND o.runtime_location::jsonb -> 'runtime' ->> 'state'
              = s.runtime_location::jsonb -> 'runtime' ->> 'state')
"""


def upgrade() -> None:
    op.execute(sa.text(DROP_BORROWED_LOCATIONS))


def downgrade() -> None:
    """The cleared locations named runners their rows did not own; there is
    nothing to put back."""
    pass
