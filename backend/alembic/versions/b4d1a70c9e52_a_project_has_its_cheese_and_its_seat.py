"""every project has its 芝士 and a seat for it in its own room

Revision ID: b4d1a70c9e52
Revises: a7f1c0d4e2b9
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
    profile that keeps it rendering as 芝士;
  - and a seat for it in the project's own room.

Pure INSERT, so dev does not stop: rows the old image cannot see do not change
what it does, and a project it creates during the window has no row until the
new image's re-seed (`AgentInstanceService.materialize_default`) makes one.
Room-derived stand-in seats are left exactly where they are — nothing here
revokes a seat, and retiring that naming is a separate step.

Idempotent on ``(project_id, handle='cheese')`` and on every seat it inserts, so
a rerun changes nothing.

Downgrade restores nothing: these are the rows a project is supposed to have,
and deleting an agent would strand the memory pool keyed by its handle.
"""

import json
import uuid
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b4d1a70c9e52"
down_revision: str | Sequence[str] | None = "a7f1c0d4e2b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Every project's default agent, with the handle it sits on rosters as and the
# room to seat it in. Same derivation as `identity.handles.agent_instance_handle`.
SEATS = """
    CREATE TEMP TABLE cheese_seats AS
    SELECT p.id AS project_id,
           p.root_topic_id,
           a.id AS instance_id,
           'cheese-' || left(replace(a.id::text, '-', ''), 12) AS seat
      FROM projects p
      JOIN agent_instances a ON a.id = p.default_agent_instance_id
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
        configuration = {
            "body": "",
            "model": initial_model(project["settings"] or {}, harness),
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
    # execution binding is what says so — and without the profile every 芝士
    # would render as its raw handle.
    op.execute(
        """
        INSERT INTO "user" (id, username, email, email_domain, created_at, updated_at)
        SELECT nextval('user_id_seq'), q.seat, q.seat || '@agent.cheese.local',
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
    op.execute(
        """
        INSERT INTO user_profile (user_id, nickname, intro, avatar_id, created_at, updated_at)
        SELECT u.id, '芝士', '', 1, now(), now()
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
    op.execute("DROP TABLE cheese_seats")


def downgrade() -> None:
    pass
