"""every project has its 芝士 and a seat for it in its own room

Revision ID: b4d1a70c9e52
Revises: c1a7e05d4b83
Create Date: 2026-09-20 11:00:00

A project used to be able to have no agent row at all: 「项目的芝士」 was an
implicit default that existed only as a value in memory, keyed by the ``cheese``
handle. Nothing could seat it, so authorization, attribution and 「谁答这一句」
each had to ask the project instead of reading a seat.

Code now creates a project with its 芝士 and with that 芝士's seat in the
project's own room. This backfills the projects that were created before it:

  - a project that never chose an agent gets one row, handle ``cheese``, with
    the configuration a new agent would be created with on this deployment;
  - every project's default agent gets the identity it acts under — a user row,
    its execution binding (that is what makes a user an agent) and the display
    profile that keeps it rendering under the name the agent carries;
  - a seat for it in the project's own room;
  - and, where 总览 was still seating the room-derived stand-in (``cheese-<12
    hex of the topic id>``) that answered before the agent had a seat, that
    stand-in's lines are re-attributed to the seat and its roster row goes.
    `f3a8c5d2e917` did exactly this for every room it could resolve, and the
    rooms it skipped are the ones this migration resolves: a project with no
    default agent left it no way to name who the stand-in stood in for. Left
    standing, the stand-in would be a second 芝士 on the same roster, and
    revoking the agent's seat would no longer close its write gate — the
    stand-in carries an execution binding of its own, so the roster would go on
    answering 「这里有个 agent」.

A 总览 that seats some OTHER agent as well is left alone, as it was there: with
more than one candidate nothing can say which of them the stand-in was.

Pure INSERT for the rows a project is supposed to have, so dev does not stop:
rows the old image cannot see do not change what it does.

The old image goes on creating projects until the container is swapped, and what
it creates is an agent row and a pointer with no seat — it seats the room-derived
stand-in instead. `materialize_default`'s re-seed does not catch those: it seeds
an agent that is missing, and theirs is not missing. Re-seating on every read
would catch them and would also undo every revoked seat on the next read, which
is the one capability a seat exists to provide, so this backfill is what catches
them — named in P11's migration (`refactor(identity): an agent signs with its
instance handle`, the next migration to deploy) to run once more, unchanged. It
is idempotent, so the second run costs a scan and changes only those projects.

Idempotent on ``(project_id, handle='cheese')``, on every seat it inserts and on
the stand-in retirement (which fires only while the stand-in is still seated),
so a rerun changes nothing.

Downgrade restores nothing: these are the rows a project is supposed to have,
and deleting an agent would strand the memory pool keyed by its handle.
"""

import json
import uuid
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b4d1a70c9e52"
down_revision: str | Sequence[str] | None = "c1a7e05d4b83"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Every project's default agent, with the handle it sits on rosters as, the name
# it renders under and the room to seat it in. Same derivation as
# `identity.handles.agent_instance_handle`.
SEATS = """
    CREATE TEMP TABLE cheese_seats AS
    SELECT p.id AS project_id,
           p.root_topic_id,
           a.id AS instance_id,
           a.display_name AS display_name,
           'cheese-' || left(replace(a.id::text, '-', ''), 12) AS seat
      FROM projects p
      JOIN agent_instances a ON a.id = p.default_agent_instance_id
"""

# The 总览 rooms where the seat just inserted replaces a room-derived stand-in:
# the stand-in is still on the roster, and no agent other than this one sits
# there, so it is the one the stand-in stood in for.
STAND_INS = """
    CREATE TEMP TABLE cheese_stand_ins AS
    SELECT s.root_topic_id AS topic_id,
           'cheese-' || left(replace(s.root_topic_id::text, '-', ''), 12) AS stand_in,
           s.seat AS own
      FROM cheese_seats s
     WHERE s.root_topic_id IS NOT NULL
       AND EXISTS (
           SELECT 1 FROM topic_memberships tm
            WHERE tm.topic_id = s.root_topic_id
              AND tm.member_handle =
                  'cheese-' || left(replace(s.root_topic_id::text, '-', ''), 12))
       AND NOT EXISTS (
           SELECT 1
             FROM topic_memberships tm
             JOIN agent_instances other
               ON tm.member_handle =
                  'cheese-' || left(replace(other.id::text, '-', ''), 12)
            WHERE tm.topic_id = s.root_topic_id
              AND other.id <> s.instance_id)
"""


def upgrade() -> None:
    # Deployment defaults are read once here, exactly as agent creation reads
    # them, so a backfilled 芝士 starts on the same model as a new one.
    from app.domain.agent.harness import harness_name
    from app.domain.agent_instance.configuration import initial_model

    bind = op.get_bind()
    harness = harness_name(None)
    unseeded = (
        bind.execute(
            sa.text(
                """
                SELECT p.id, p.settings
                  FROM projects p
                 WHERE p.default_agent_instance_id IS NULL
                   AND NOT EXISTS (
                       SELECT 1 FROM agent_instances a
                        WHERE a.project_id = p.id AND a.handle = 'cheese')
                """
            )
        )
        .mappings()
        .all()
    )
    for project in unseeded:
        model = initial_model(project["settings"] or {}, harness)
        configuration = {
            "body": "",
            "model": model,
            "harness": harness,
            "skills": [],
            "mcp_servers": [],
            "effort": None,
        }
        bind.execute(
            sa.text(
                """
                INSERT INTO agent_instances
                    (id, project_id, handle, type_name, display_name,
                     configuration, is_active, created_at, updated_at)
                VALUES (:id, :project, 'cheese', NULL, '芝士',
                        CAST(:configuration AS json), true, now(), now())
                """
            ),
            {
                "id": uuid.uuid4(),
                "project": project["id"],
                "configuration": json.dumps(configuration),
            },
        )
    # A project points at its 芝士: that pointer is what 「新房间跟谁开」 reads.
    op.execute(
        """
        UPDATE projects p SET default_agent_instance_id = a.id
          FROM agent_instances a
         WHERE a.project_id = p.id AND a.handle = 'cheese'
           AND p.default_agent_instance_id IS NULL
        """
    )

    op.execute(SEATS)
    # The identity an agent acts under. A user row alone is not an agent — the
    # execution binding is what says so — and without the profile every agent
    # would render as its raw handle.
    #
    # ``user.id`` is GENERATED BY DEFAULT AS IDENTITY (`718ecf7d61d9`), so the
    # column allocates it. Naming a sequence here would pick the wrong one: the
    # `user_id_seq` the initial schema created is a leftover that nothing has
    # advanced since `219831eb75a3` seeded the demo users, and reading it hands
    # out ids the table already holds.
    op.execute(
        """
        INSERT INTO "user" (username, email, email_domain, created_at, updated_at)
        SELECT q.seat, q.seat || '@agent.cheese.local',
               'agent.cheese.local', now(), now()
          FROM (SELECT DISTINCT s.seat
                  FROM cheese_seats s
                 WHERE NOT EXISTS (
                     SELECT 1 FROM "user" u WHERE u.username = s.seat)) q
        """
    )
    op.execute(
        """
        INSERT INTO agent_bindings (id, user_id, kind, created_at, updated_at)
        SELECT gen_random_uuid(), u.id, 'platform', now(), now()
          FROM cheese_seats s
          JOIN "user" u ON u.username = s.seat
         WHERE NOT EXISTS (SELECT 1 FROM agent_bindings b WHERE b.user_id = u.id)
        """
    )
    # It renders under the name saved on the agent, the way
    # `IdentityService.ensure_instance_agent_user` reads it: a default agent that
    # was renamed keeps its name. A profile is written once and never updated
    # afterwards, so writing 芝士 here would rename that agent for good.
    op.execute(
        """
        INSERT INTO user_profile (user_id, nickname, intro, avatar_id, created_at, updated_at)
        SELECT u.id, COALESCE(NULLIF(btrim(s.display_name), ''), '芝士'), '', 1, now(), now()
          FROM cheese_seats s
          JOIN "user" u ON u.username = s.seat
         WHERE NOT EXISTS (SELECT 1 FROM user_profile pr WHERE pr.user_id = u.id)
        """
    )
    # The seat itself, in the project's own room.
    op.execute(
        """
        INSERT INTO topic_memberships (id, topic_id, member_handle, role, created_at, updated_at)
        SELECT gen_random_uuid(), s.root_topic_id, s.seat, 'member', now(), now()
          FROM cheese_seats s
         WHERE s.root_topic_id IS NOT NULL
        ON CONFLICT (topic_id, member_handle) DO NOTHING
        """
    )

    # And the stand-in it replaces leaves 总览, its lines re-attributed — the
    # same shape `f3a8c5d2e917` used, now that these rooms resolve.
    op.execute(STAND_INS)
    op.execute(
        """
        UPDATE blocks b SET author = m.own
          FROM cheese_stand_ins m
         WHERE b.topic_id = m.topic_id AND b.author = m.stand_in
        """
    )
    # A reaction the agent already left under its own seat wins over the
    # stand-in's copy of the same emoji.
    op.execute(
        """
        DELETE FROM block_reactions br
         USING cheese_stand_ins m, blocks b
         WHERE b.id = br.block_id AND b.topic_id = m.topic_id
           AND br.author = m.stand_in
           AND EXISTS (SELECT 1 FROM block_reactions o
                        WHERE o.block_id = br.block_id AND o.emoji = br.emoji
                          AND o.author = m.own)
        """
    )
    op.execute(
        """
        UPDATE block_reactions br SET author = m.own
          FROM cheese_stand_ins m, blocks b
         WHERE b.id = br.block_id AND b.topic_id = m.topic_id
           AND br.author = m.stand_in
        """
    )
    op.execute(
        """
        DELETE FROM topic_memberships tm
         USING cheese_stand_ins m
         WHERE tm.topic_id = m.topic_id AND tm.member_handle = m.stand_in
        """
    )
    op.execute("DROP TABLE cheese_stand_ins")
    op.execute("DROP TABLE cheese_seats")


def downgrade() -> None:
    pass
