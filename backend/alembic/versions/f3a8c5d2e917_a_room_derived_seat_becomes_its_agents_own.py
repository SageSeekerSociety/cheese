"""a room-derived seat becomes the seat of the agent it stood in for

Revision ID: f3a8c5d2e917
Revises: e7d2b91a4c06
Create Date: 2026-09-19 09:00:00

Before agents had identities of their own, every room got a seat derived from
the room (`cheese-<12 hex of the topic id>`) and "the room's agent" answered
under it. An agent is one collaborator across every room it sits in, so a
room-derived seat is a stand-in for whichever agent was answering there:

  - a room that seats exactly one saved teammate (the one PR #1219 moved onto
    the roster, or a DM's peer): the stand-in was that teammate;
  - a room that seats none: the stand-in was the project's default, which is
    seated now under its own identity;
  - a room that seats several (nothing addressed the stand-in unambiguously):
    left alone.

For every resolved room the stand-in's messages and reactions are re-attributed
to the agent's own seat and the stand-in's roster row is dropped, so a roster
shows one row per teammate and every AI line is labelled by its author. The
stand-in's user row stays: a room's compute screen still acts under it, and a
token naming it resolves to the agent seated in the room (see
`ActorResolver`). The memory pool the stand-in wrote to stays where it is; the
room keeps reading it as its legacy tail.

Downgrade restores nothing: which seat a message was under is not information
anyone wants back.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "f3a8c5d2e917"
down_revision: str | Sequence[str] | None = "d8e3f5a71b22"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# room → (stand-in seat, the agent's own seat), for every room that resolves.
RESOLVED = """
    WITH seats AS (
        SELECT t.id AS topic_id,
               'cheese-' || left(replace(t.id::text, '-', ''), 12) AS stand_in,
               p.default_agent_instance_id AS default_id
          FROM topics t
          JOIN projects p ON p.id = t.project_id
    ),
    seated AS (
        SELECT s.topic_id,
               count(ai.id) AS n,
               min('cheese-' || left(replace(ai.id::text, '-', ''), 12)) AS seat
          FROM seats s
          JOIN topic_memberships tm ON tm.topic_id = s.topic_id
          JOIN agent_instances ai
            ON tm.member_handle = 'cheese-' || left(replace(ai.id::text, '-', ''), 12)
         GROUP BY s.topic_id
    ),
    resolved AS (
        SELECT s.topic_id, s.stand_in,
               CASE
                 WHEN seated.n = 1 THEN seated.seat
                 WHEN seated.n IS NULL AND s.default_id IS NOT NULL
                   THEN 'cheese-' || left(replace(s.default_id::text, '-', ''), 12)
               END AS own
          FROM seats s
          LEFT JOIN seated ON seated.topic_id = s.topic_id
         WHERE EXISTS (SELECT 1 FROM topic_memberships x
                        WHERE x.topic_id = s.topic_id AND x.member_handle = s.stand_in)
    )
    SELECT topic_id, stand_in, own FROM resolved WHERE own IS NOT NULL
"""


def upgrade() -> None:
    op.execute(f"CREATE TEMP TABLE seat_moves AS {RESOLVED}")
    # The agent's own seat joins every room it stood in for.
    op.execute(
        """
        INSERT INTO topic_memberships (id, topic_id, member_handle, role, created_at, updated_at)
        SELECT gen_random_uuid(), m.topic_id, m.own, 'member', now(), now()
          FROM seat_moves m
        ON CONFLICT (topic_id, member_handle) DO NOTHING
        """
    )
    # Its messages and reactions are its own. A reaction the agent already left
    # under its own seat wins over the stand-in's copy of the same emoji.
    op.execute(
        """
        UPDATE blocks b SET author = m.own
          FROM seat_moves m
         WHERE b.topic_id = m.topic_id AND b.author = m.stand_in
        """
    )
    op.execute(
        """
        DELETE FROM block_reactions br
         USING seat_moves m, blocks b
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
          FROM seat_moves m, blocks b
         WHERE b.id = br.block_id AND b.topic_id = m.topic_id
           AND br.author = m.stand_in
        """
    )
    op.execute(
        """
        DELETE FROM topic_memberships tm
         USING seat_moves m
         WHERE tm.topic_id = m.topic_id AND tm.member_handle = m.stand_in
        """
    )
    op.execute("DROP TABLE seat_moves")


def downgrade() -> None:
    pass
