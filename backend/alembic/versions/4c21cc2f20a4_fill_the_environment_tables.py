"""Fill the environment tables from the sessions' leases

Revision ID: 4c21cc2f20a4
Revises: a7e2c4f19b30

Until this release a session's execution environment lived on the session
row: what it was on in ``agent_sessions.work_lease``, its generation, the
notices its agent was owed, a thaw it asked for, the host it asked to leave
and the directories it left on people's machines in
``agent_sessions.execution_request``, and the sandbox it worked in named it
by ``sandbox_homes.session_id``. From this release they are rows of their own
(revision a7e2c4f19b30), and this fills them from those columns:

- A session with a lease gets an environment of its own as the lease
  describes it (its machine, directory, room generation, status, the install
  running in it and what its install left), naming the sandbox that named
  the session, and is attached to it under the lease's generation, with the
  attachment named by the session's id so a running command's
  ``CHEESE_ATTACHMENT`` still resolves. A released lease gives a released
  environment, still attached. The choice it is of is the agent's own
  (``agent_environments``), or the conversation's default where the agent
  has none written.
- What its agent was owed (a lost or parked sandbox, with what was running)
  becomes the session's ``environment_notice``; a thaw it asked for marks its
  sandbox (``sandbox_homes.thaw_wanted_at``).
- Each conversation points to the place its work is in: the environment of
  its sessions that is of the conversation's default choice and current room
  generation (one still answering before a released one, then the one placed
  last; an agent on its owner's computer never counts as the default's), and
  when none is, the environment still answering of the session placed last,
  an agent on its owner's computer included. The host a session asked to
  leave is the pointer's.
- A private chat's lease is no environment: it is the scratch container of
  the session's own process, kept with where that process runs
  (``runtime_location["scratch"]``) for the turns already running.
- The directories a session left on people's machines
  (``retained_leases``) become environments nobody is attached to and no
  conversation points to, for the room's cleanup to find.

The previous release reads and writes only the old columns while this one
deploys, so the rows here are a snapshot of them. The reconciliation can be
run again (the migration that drops those columns runs it once more first):
it takes only the sessions it has not taken before and that nothing has
attached since, so what the previous release wrote for a session after this
ran is picked up and nothing the new release did is overwritten. Which
sessions it took is kept in ``environment_backfill_marks``, which goes with
the old columns.

Plain SQL; no ``app`` import. The JSON shapes are copied from
``app.domain.agent.environment`` (``Sandbox``, ``SelfHosted``,
``STANDARD``), ``app.domain.agent.owner_provider.OWNER_CHANNEL`` and the keys
the previous release's ``agent_session.models`` named.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "4c21cc2f20a4"
down_revision: str | Sequence[str] | None = "a7e2c4f19b30"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MARKS = """
CREATE TABLE IF NOT EXISTS environment_backfill_marks (
    session_id uuid PRIMARY KEY
)
"""

#: An environment choice as ``model_dump`` gives it, from any stored form: a
#: self-hosted machine by its device, anything else a sandbox of its spec
#: (``standard`` when it names none).
_CHOICE = """
CASE WHEN ({0}) ->> 'kind' = 'self_hosted'
     THEN jsonb_build_object('kind', 'self_hosted', 'device_id', ({0}) ->> 'device_id')
     ELSE jsonb_build_object('kind', 'sandbox', 'spec', coalesce(({0}) ->> 'spec', 'standard'))
END
"""


def _stored(column: str) -> str:
    """A stored choice, or NULL where none is (`environment.stored`)."""
    return f"nullif(nullif(({column})::jsonb, 'null'::jsonb), '{{}}'::jsonb)"


#: The sessions this run takes, with what it derives for each.
TODO = f"""
CREATE TEMP TABLE environment_backfill_todo ON COMMIT DROP AS
WITH taken AS (
    SELECT s.id AS session_id,
           s.conversation_id,
           s.agent_handle,
           s.placed_at,
           s.updated_at,
           nullif(s.work_lease::jsonb, 'null'::jsonb) AS lease,
           coalesce(nullif(s.execution_request::jsonb, 'null'::jsonb), '{{}}'::jsonb)
               AS request,
           coalesce(s.runtime_location::jsonb ->> 'channel', '') = 'owner'
               AS own_computer,
           c.kind = 'personal' AS personal,
           CASE WHEN c.kind = 'personal' THEN c.id
                ELSE coalesce(
                    (SELECT t.room_id FROM tasks t WHERE t.id = c.id),
                    (SELECT th.room_id FROM threads th WHERE th.id = c.id),
                    c.id)
           END AS holder_id
      FROM agent_sessions s
      JOIN conversations c ON c.id = s.conversation_id
     WHERE (s.work_lease IS NOT NULL OR s.execution_request IS NOT NULL)
       AND NOT EXISTS (SELECT 1 FROM environment_attachments a
                        WHERE a.session_id = s.id)
       AND NOT EXISTS (SELECT 1 FROM environment_backfill_marks m
                        WHERE m.session_id = s.id)
),
placed AS (
    SELECT taken.*,
           holder.project_id,
           CASE WHEN taken.personal THEN taken.holder_id::text
                ELSE coalesce(room.resource_id::text, taken.holder_id::text)
           END AS generation,
           CASE WHEN taken.personal THEN NULL
                ELSE coalesce(
                    {_stored("(SELECT t.default_environment FROM tasks t WHERE t.id = taken.conversation_id)")},
                    {_stored("room.default_environment")},
                    {_stored("project.settings::jsonb -> 'execution_environment'")})
           END AS stored_default,
           CASE WHEN taken.personal THEN NULL
                ELSE {_stored("own.environment")}
           END AS stored_own
      FROM taken
      JOIN conversations holder ON holder.id = taken.holder_id
      LEFT JOIN topics room ON room.id = taken.holder_id
      LEFT JOIN projects project ON project.id = holder.project_id
      LEFT JOIN agent_environments own
             ON own.conversation_id = taken.conversation_id
            AND own.agent_handle = taken.agent_handle
)
SELECT placed.*,
       gen_random_uuid() AS environment_id,
       {_CHOICE.format("coalesce(placed.stored_default, '{}'::jsonb)")} AS default_choice,
       {_CHOICE.format("coalesce(placed.stored_own, placed.stored_default, '{}'::jsonb)")}
           AS choice,
       (SELECT h.id FROM sandbox_homes h WHERE h.session_id = placed.session_id
         LIMIT 1) AS home_id
  FROM placed
"""

#: The keys of a lease that are columns of the environment or of the
#: attachment, or said by the row itself; the rest is what its install left.
_NOT_EXECUTOR = (
    "kind",
    "session_id",
    "generation",
    "setup_digest",
    "device_id",
    "resource_id",
    "room_resource_id",
    "status",
    "claim",
    "claim_until",
    "verified_at",
)
_EXECUTOR = "({0}) - " + " - ".join(f"'{key}'" for key in _NOT_EXECUTOR)

ENVIRONMENTS = f"""
INSERT INTO environments (
    id, topic_id, project_id, device_id, host_id, sandbox_home_id, resource_id,
    room_resource_id, choice, executor, status, claim, claim_until, verified_at,
    created_at, updated_at)
SELECT todo.environment_id,
       todo.holder_id,
       todo.project_id,
       todo.lease ->> 'device_id',
       home.host_id,
       home.id,
       coalesce(todo.lease ->> 'resource_id', todo.request ->> 'generation',
                todo.lease ->> 'generation', gen_random_uuid()::text),
       coalesce(todo.lease ->> 'room_resource_id', todo.generation),
       todo.choice,
       {_EXECUTOR.format("todo.lease")},
       coalesce(todo.lease ->> 'status', 'ready'),
       todo.lease ->> 'claim',
       (todo.lease ->> 'claim_until')::timestamptz,
       (todo.lease ->> 'verified_at')::timestamptz,
       now(),
       now()
  FROM environment_backfill_todo todo
  LEFT JOIN sandbox_homes home ON home.id = todo.home_id
 WHERE todo.lease ->> 'device_id' IS NOT NULL
   AND coalesce(todo.lease ->> 'kind', 'device') = 'device'
"""

ATTACHMENTS = """
INSERT INTO environment_attachments (
    id, environment_id, session_id, generation, setup_digest, acted_at)
SELECT todo.session_id,
       todo.environment_id,
       todo.session_id,
       coalesce(todo.lease ->> 'generation', todo.request ->> 'generation',
                gen_random_uuid()::text),
       todo.lease ->> 'setup_digest',
       coalesce(todo.updated_at, now())
  FROM environment_backfill_todo todo
 WHERE todo.lease ->> 'device_id' IS NOT NULL
   AND coalesce(todo.lease ->> 'kind', 'device') = 'device'
"""

#: `LOST_KEY`, `LOST_PROCESSES_KEY`, `PARKED_KEY` of the previous release.
NOTICES = """
UPDATE agent_sessions s
   SET environment_notice = jsonb_strip_nulls(jsonb_build_object(
         'lost', CASE WHEN coalesce((todo.request ->> 'sandbox_lost')::boolean, false)
                      THEN true END,
         'lost_processes', todo.request -> 'sandbox_lost_processes',
         'parked', todo.request -> 'sandbox_parked'))
  FROM environment_backfill_todo todo
 WHERE s.id = todo.session_id
   AND s.environment_notice IS NULL
   AND (coalesce((todo.request ->> 'sandbox_lost')::boolean, false)
        OR todo.request ? 'sandbox_parked')
"""

#: `THAW_KEY` of the previous release: the resource of the frozen sandbox a
#: tool call asked to wake.
THAWS = """
UPDATE sandbox_homes home
   SET thaw_wanted_at = now()
  FROM environment_backfill_todo todo
 WHERE home.id = todo.home_id
   AND home.resource_id = todo.request ->> 'sandbox_thaw'
   AND home.thaw_wanted_at IS NULL
"""

#: `LEFT_HOST_KEY` of the previous release.
POINTERS = """
INSERT INTO conversation_environments (conversation_id, environment_id, left_host_id)
SELECT conversation_id, environment_id, left_host_id
  FROM (
    SELECT DISTINCT ON (todo.conversation_id)
           todo.conversation_id,
           coalesce(
             (SELECT pick.environment_id
                FROM environment_backfill_todo pick
                JOIN environments env ON env.id = pick.environment_id
               WHERE pick.conversation_id = todo.conversation_id
                 AND NOT pick.own_computer
                 AND env.choice = pick.default_choice
                 AND env.room_resource_id = pick.generation
               ORDER BY env.status = 'released', pick.placed_at DESC NULLS LAST,
                        pick.session_id
               LIMIT 1),
             (SELECT pick.environment_id
                FROM environment_backfill_todo pick
                JOIN environments env ON env.id = pick.environment_id
               WHERE pick.conversation_id = todo.conversation_id
                 AND env.status <> 'released'
               ORDER BY pick.placed_at DESC NULLS LAST, pick.session_id
               LIMIT 1)) AS environment_id,
           (SELECT host.id
              FROM environment_backfill_todo asked
              JOIN cloud_hosts host
                ON host.id::text = asked.request ->> 'sandbox_left_host'
             WHERE asked.conversation_id = todo.conversation_id
             LIMIT 1) AS left_host_id
      FROM environment_backfill_todo todo
     ORDER BY todo.conversation_id
  ) chosen
 WHERE environment_id IS NOT NULL OR left_host_id IS NOT NULL
ON CONFLICT (conversation_id) DO NOTHING
"""

#: Directories a session left on people's machines: environments nobody is
#: attached to, for the room's cleanup. What choice they were made for was
#: never recorded; nothing compares it, since an environment no pointer names
#: and nobody is attached to is never handed out again.
RETAINED = f"""
INSERT INTO environments (
    id, topic_id, project_id, device_id, resource_id, room_resource_id, choice,
    executor, status, created_at, updated_at)
SELECT gen_random_uuid(),
       todo.holder_id,
       todo.project_id,
       kept ->> 'device_id',
       coalesce(kept ->> 'resource_id', kept ->> 'generation',
                gen_random_uuid()::text),
       coalesce(kept ->> 'room_resource_id', todo.generation),
       jsonb_build_object('kind', 'self_hosted', 'device_id', kept ->> 'device_id'),
       {_EXECUTOR.format("kept")},
       'released',
       now(),
       now()
  FROM environment_backfill_todo todo
 CROSS JOIN LATERAL jsonb_array_elements(
       CASE WHEN jsonb_typeof(todo.request -> 'retained_leases') = 'array'
            THEN todo.request -> 'retained_leases' ELSE '[]'::jsonb END) AS kept
 WHERE kept ->> 'device_id' IS NOT NULL
"""

#: A private chat's lease (``kind: private``) is no environment: it is the
#: scratch container of the session's own process, which a turn now keeps
#: with where that process runs (``runtime_location["scratch"]``) and writes
#: again whenever it starts. Kept there for the turns already running.
SCRATCH = """
UPDATE agent_sessions s
   SET runtime_location = (
         s.runtime_location::jsonb || jsonb_build_object('scratch', todo.lease)
       )::json
  FROM environment_backfill_todo todo
 WHERE s.id = todo.session_id
   AND todo.lease ->> 'kind' = 'private'
   AND jsonb_typeof(nullif(s.runtime_location::jsonb, 'null'::jsonb)) = 'object'
   AND NOT (s.runtime_location::jsonb ? 'scratch')
"""

MARK = """
INSERT INTO environment_backfill_marks (session_id)
SELECT session_id FROM environment_backfill_todo
ON CONFLICT DO NOTHING
"""

#: In order: what a statement reads, an earlier one wrote.
RECONCILE = (
    TODO,
    ENVIRONMENTS,
    ATTACHMENTS,
    NOTICES,
    THAWS,
    POINTERS,
    RETAINED,
    SCRATCH,
    MARK,
)


def upgrade() -> None:
    op.execute(sa.text(MARKS))
    for statement in RECONCILE:
        op.execute(sa.text(statement))


def downgrade() -> None:
    """The previous release reads only the old columns, which this never
    changed: the rows here are left for the next upgrade to keep."""
    pass
