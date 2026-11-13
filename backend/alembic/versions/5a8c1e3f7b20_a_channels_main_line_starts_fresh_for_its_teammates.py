"""A channel's main line starts fresh for its AI teammates

Revision ID: 5a8c1e3f7b20
Revises: c7e2a5d9f031
Create Date: 2026-11-13

From this release a message that calls 芝士 in a channel's main line is
answered there, by a session the channel holds for it. From #2846 (2026-10-06)
until now it was answered in a 支线 opened under the message, and the main line
had no session: what a teammate last read there, and the session it had there,
date from before that. Left alone, the first call in an old channel would hand
the teammate the main line's newest 40 messages since then as its input —
questions already answered in their 支线 among them — and a session whose
transcript is still on a machine would resume a conversation from October.

So, for every channel (a room that is not a private chat; not a task, not a
支线):

- each AI seat's read cursor there (`agent_reads`) moves to the newest block
  of the main line, for the seats on the channel's roster and any seat that
  already had a cursor there. A cursor only moves forward: one already at or
  past that block is left where it is.
- each session row there (`agent_sessions`) forgets what it resumes by and what
  it was last told (`resume_token`, `told`), so the first turn opens a new
  conversation with the whole opening. The rows stay: a sandbox home names its
  session by id (`machine.lifecycle`), with no foreign key that would say so,
  and a session's lease and environment rows are the room's machine choice.

The release still serving runs no turn in a channel's main line, so nothing is
writing these rows while this runs. Both statements are set-based and
idempotent: run again, they find nothing to move and nothing to forget.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "5a8c1e3f7b20"
down_revision: str | Sequence[str] | None = "c7e2a5d9f031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: A room that is a channel: not a private chat. Tasks and 支线 are
#: conversations of their own kind (`conversation.models.ConversationKind`).
_CHANNELS = """
    SELECT t.id
    FROM topics t
    JOIN conversations c ON c.id = t.id
    WHERE c.kind = 'room' AND t.is_private IS FALSE
"""

#: An AI seat's handle, as `identity.handles.agent_handle_column` spells it.
_AGENT_SEAT = "(m.member_handle = 'cheese' OR m.member_handle LIKE 'cheese-%')"


def upgrade() -> None:
    op.execute(
        f"""
        WITH channels AS ({_CHANNELS}),
        newest AS (
            SELECT ch.id AS conversation_id, b.created_at, b.id AS block_id
            FROM channels ch
            CROSS JOIN LATERAL (
                SELECT created_at, id
                FROM blocks
                WHERE conversation_id = ch.id
                ORDER BY created_at DESC, id DESC
                LIMIT 1
            ) b
        ),
        seats AS (
            SELECT m.topic_id AS conversation_id, m.member_handle AS agent_handle
            FROM topic_memberships m
            JOIN channels ch ON ch.id = m.topic_id
            WHERE {_AGENT_SEAT}
            UNION
            SELECT r.conversation_id, r.agent_handle
            FROM agent_reads r
            JOIN channels ch ON ch.id = r.conversation_id
        )
        INSERT INTO agent_reads (
            conversation_id, agent_handle, read_through_at,
            read_through_block_id, updated_at
        )
        SELECT s.conversation_id, s.agent_handle, n.created_at, n.block_id, now()
        FROM seats s
        JOIN newest n ON n.conversation_id = s.conversation_id
        ON CONFLICT (conversation_id, agent_handle) DO UPDATE
        SET read_through_at = EXCLUDED.read_through_at,
            read_through_block_id = EXCLUDED.read_through_block_id,
            updated_at = EXCLUDED.updated_at
        WHERE (agent_reads.read_through_at, agent_reads.read_through_block_id)
            < (EXCLUDED.read_through_at, EXCLUDED.read_through_block_id)
        """
    )
    op.execute(
        f"""
        UPDATE agent_sessions
        SET resume_token = NULL, told = NULL
        WHERE conversation_id IN ({_CHANNELS})
          AND (resume_token IS NOT NULL OR told IS NOT NULL)
        """
    )


def downgrade() -> None:
    """Nothing to undo, and no way to: a cursor moved forward reads like one a
    turn moved, and a forgotten resume token is gone. The release before this
    one answers a main-line call in a 支线 and reads neither, so both are left
    where they are."""
