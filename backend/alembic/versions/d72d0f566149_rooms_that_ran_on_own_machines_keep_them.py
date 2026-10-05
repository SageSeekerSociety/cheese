"""Rooms that already ran on an enrolled machine keep the whole machine

A room on an enrolled machine now runs isolated unless it is bound `host`
(#2320 step 2). Rooms that named a machine were bound `host` when they named
it, but a room on automatic selection was never bound: the machine its
sessions took held no record of the room, and it saw the whole machine
because every enrolled machine did. Left so, such a room would start its next
session isolated, losing the tools under the owner's home and the owner's
Docker, or be refused on macOS.

This binds each such room, `host`, to the enrolled machine its most recently
placed session holds its lease on. That is the machine its next session
returns to (`session_work._attempt` takes the session's own lease, then the
room's others). A room whose sessions used several enrolled machines is bound
to that one only: binding is one machine per room, and the others are where
it no longer works; moving it back to one of them is a new choice and starts
isolated there, as any move does. A room whose latest lease is on a Cloud
machine, or whose own choice is now Cloud, is not bound, since a binding to an
enrolled machine would point its environment status at the wrong machine; and
a room already bound keeps its binding.

The downgrade leaves the rows: the code before this treated every binding to
an enrolled machine as `host` whatever it said, so they change nothing there.

Revision ID: d72d0f566149
Revises: 4383bf20b465
Create Date: 2026-10-04
"""

from alembic import op

revision = "d72d0f566149"
down_revision = "4383bf20b465"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO device_topic (topic_id, device_id, visibility)
        SELECT latest.topic_id, latest.device_id, 'host'
        FROM (
            SELECT DISTINCT ON (s.topic_id)
                s.topic_id, s.work_lease ->> 'device_id' AS device_id
            FROM agent_sessions AS s
            WHERE s.work_lease ->> 'device_id' IS NOT NULL
            ORDER BY s.topic_id, s.placed_at DESC NULLS LAST, s.id DESC
        ) AS latest
        JOIN device AS d
            ON d.device_id = latest.device_id AND d.supply = 'self_hosted'
        JOIN topics AS t ON t.id = latest.topic_id
        WHERE COALESCE(t.compute_config ->> 'profile', '') <> 'cloud'
        ON CONFLICT (topic_id) DO NOTHING
        """
    )


def downgrade() -> None:
    pass
