"""Channels are joined: members by choice, 综合 by being in the project

- **`topics.description`**: what a channel is for, written by whoever manages it.
- **`topic_read_states.notify_level`** takes three values now: `all`, `mentions`
  (the default) and `mute`; **`muted_until`** ends a mute that was set for a
  while. A stored `all` was the old default nobody chose, so it becomes the new
  default.
- **A channel has no admins.** It is managed by whoever created it (`owner`)
  and whoever manages the project, so an `admin` seat becomes a `member`.
- **综合 seats nobody by name.** Everyone in the project is in it, which the
  project's roster already answers; the person rows `seed_root` wrote are
  deleted. Its AI teammates keep their seats.
- **Who is in each other channel**: whoever spoke in it in the last 30 days (its
  main line or a 支线 under it) and everyone an open task there is assigned to,
  as long as they are still in the project.

Revision ID: d3a8e51c07f2
Revises: e8e05b3cfe1f
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d3a8e51c07f2"
down_revision: str | Sequence[str] | None = "e8e05b3cfe1f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _lock(tables: str) -> None:
    """As in b6fcc6362b79: queue for every table, a few seconds at a time."""
    op.execute(f"""
        DO $$
        DECLARE
            attempts integer := 0;
            outer_timeout text := current_setting('lock_timeout');
        BEGIN
            PERFORM set_config('lock_timeout', '3s', true);
            LOOP
                BEGIN
                    LOCK TABLE {tables} IN ACCESS EXCLUSIVE MODE;
                    EXIT;
                EXCEPTION WHEN lock_not_available OR deadlock_detected THEN
                    attempts := attempts + 1;
                    IF attempts >= 100 THEN
                        RAISE;
                    END IF;
                    PERFORM pg_sleep(0.2);
                END;
            END LOOP;
            PERFORM set_config('lock_timeout', outer_timeout, true);
        END
        $$
    """)


# The people of each project, as `roster()` reads them: its owner, its external
# members and its team, less whoever left it (the owner never leaves), less
# every AI teammate.
_PEOPLE = """
    WITH listed AS (
        SELECT p.id AS project_id, p.owner_handle AS handle
        FROM projects p WHERE p.owner_handle IS NOT NULL
        UNION
        SELECT pm.project_id, pm.user_handle FROM project_members pm
        UNION
        SELECT p.id, u.username
        FROM projects p
        JOIN team_user_relation r ON r.team_id = p.team_id AND r.deleted_at IS NULL
        JOIN "user" u ON u.id = r.user_id AND u.deleted_at IS NULL
    )
    SELECT l.project_id, l.handle
    FROM listed l JOIN projects p ON p.id = l.project_id
    WHERE l.handle <> 'cheese' AND l.handle NOT LIKE 'cheese-%'
      AND NOT EXISTS (
          SELECT 1 FROM "user" u JOIN agent_bindings b ON b.user_id = u.id
          WHERE u.username = l.handle
      )
      AND (
          l.handle = p.owner_handle
          OR NOT EXISTS (
              SELECT 1 FROM project_member_exclusions x
              WHERE x.project_id = l.project_id AND x.user_handle = l.handle
          )
      )
"""


def upgrade() -> None:
    _lock("topics, topic_read_states, topic_memberships")
    op.add_column(
        "topics", sa.Column("description", sa.String(length=500), nullable=True)
    )
    op.add_column(
        "topic_read_states",
        sa.Column("muted_until", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        "UPDATE topic_read_states SET notify_level = 'mentions' "
        "WHERE notify_level = 'all'"
    )
    op.alter_column("topic_read_states", "notify_level", server_default="mentions")

    op.execute("UPDATE topic_memberships SET role = 'member' WHERE role = 'admin'")
    op.execute("""
        DELETE FROM topic_memberships m
        USING topics t
        WHERE t.id = m.topic_id AND t.kind = 'root'
          AND m.member_handle <> 'cheese' AND m.member_handle NOT LIKE 'cheese-%'
          AND NOT EXISTS (
              SELECT 1 FROM "user" u JOIN agent_bindings b ON b.user_id = u.id
              WHERE u.username = m.member_handle
          )
    """)

    op.execute(f"""
        WITH people AS ({_PEOPLE}),
        channels AS (
            SELECT id, project_id FROM topics
            WHERE kind = 'topic' AND is_private = false
        ),
        took_part AS (
            SELECT c.id AS topic_id, b.author AS handle
            FROM channels c JOIN blocks b ON b.conversation_id = c.id
            WHERE b.kind = 'message' AND b.created_at > now() - interval '30 days'
            UNION
            SELECT c.id, b.author
            FROM channels c
            JOIN threads th ON th.room_id = c.id
            JOIN blocks b ON b.conversation_id = th.id
            WHERE b.kind = 'message' AND b.created_at > now() - interval '30 days'
            UNION
            SELECT c.id, tk.owner_handle
            FROM channels c JOIN tasks tk ON tk.room_id = c.id
            WHERE tk.status = 'open' AND tk.owner_handle IS NOT NULL
            UNION
            SELECT c.id, h.value
            FROM channels c
            JOIN tasks tk ON tk.room_id = c.id
            CROSS JOIN LATERAL json_array_elements_text(
                COALESCE(tk.contributor_handles::json, '[]'::json)
            ) AS h(value)
            WHERE tk.status = 'open'
        )
        INSERT INTO topic_memberships
            (id, topic_id, member_handle, role, created_at, updated_at)
        SELECT gen_random_uuid(), tp.topic_id, tp.handle, 'member', now(), now()
        FROM took_part tp
        JOIN channels c ON c.id = tp.topic_id
        JOIN people p ON p.project_id = c.project_id AND p.handle = tp.handle
        ON CONFLICT (topic_id, member_handle) DO NOTHING
    """)


def downgrade() -> None:
    raise NotImplementedError("who joined which channel is not taken back apart")
