"""The rest of the run records leave the conversation

Revision ID: 52fee3dd7773
Revises: f4378cf0084d
Create Date: 2026-10-07

b672fdeb358e left three things in the conversation that are not for it:

- Memory changes that also carried a sentence for the agent (`agent_notice`).
  Only `blocks` delivers that sentence, and only until a turn reads it
  (`consumed_turn`). A read one moves like any other record; an unread one is
  copied to `run_records` and stays in `blocks` hidden from the room
  (`in_room: false`), as the platform writes them today.
- Two kinds nothing writes any more and that were never on its list: a
  subagent starting, and the reminder to write the live document.
- Turns that did not finish, outside a 支线. Most are the platform's own
  failures from the 2026-10-03/04/06 incidents, said once per teammate per
  attempt; none of them can be retried any more. In a 支线 they stay: the
  message the 支线 hangs under says its reply failed by reading them.

A row a 支线 hangs under stays where it is, as before.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "52fee3dd7773"
down_revision: str | Sequence[str] | None = "f4378cf0084d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RECORDS = ("memory_changed", "subagent_start", "doc_missing")
FAILURES = ("turn_failed", "platform_error", "turn_timeout")
#: Not handed to the run, just the agent seats the platform itself writes as.
NOT_A_SEAT = ("system", "backend", "frontend", "accept")


def _quoted(values: Sequence[str]) -> str:
    return ", ".join("'" + value.replace("'", "''") + "'" for value in values)


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


def upgrade() -> None:
    _lock("blocks")
    move()


def move() -> None:
    """Move what the module names; keep an unread sentence for the agent."""
    kind = "b.meta ->> 'event_type'"
    op.execute(f"""
        CREATE TEMP TABLE leaving ON COMMIT DROP AS
        SELECT b.id,
               (b.meta ->> 'agent_notice') IS NOT NULL
                   AND (b.meta ->> 'consumed_turn') IS NULL AS unread
        FROM blocks b
        WHERE b.kind = 'event'
          AND b.author_type = 'platform'
          AND (
              {kind} IN ({_quoted(RECORDS)})
              OR (
                  {kind} IN ({_quoted(FAILURES)})
                  AND NOT EXISTS (
                      SELECT 1 FROM threads t WHERE t.id = b.conversation_id
                  )
              )
          )
          AND COALESCE(b.meta ->> 'in_room', '') <> 'false'
          AND NOT EXISTS (SELECT 1 FROM threads t WHERE t.root_block_id = b.id)
    """)
    op.execute(f"""
        INSERT INTO run_records (
            id, project_id, conversation_id, turn_id, seat, kind, severity,
            content, meta, created_at, updated_at
        )
        SELECT
            CASE WHEN l.unread THEN gen_random_uuid() ELSE b.id END,
            b.project_id,
            b.conversation_id,
            b.turn_id,
            COALESCE(
                b.meta ->> 'seat',
                CASE WHEN b.author NOT IN ({_quoted(NOT_A_SEAT)}) THEN b.author END
            ),
            {kind},
            COALESCE(
                b.meta ->> 'severity',
                CASE WHEN {kind} IN ({_quoted(FAILURES)}) THEN 'error' ELSE 'info' END
            ),
            b.content,
            ((b.meta::jsonb - 'agent_notice' - 'consumed_turn'))::json,
            b.created_at,
            b.updated_at
        FROM blocks b
        JOIN leaving l ON l.id = b.id
        WHERE b.created_at >= now() - interval '30 days'
    """)
    op.execute("""
        UPDATE blocks b
        SET meta = (b.meta::jsonb || '{"in_room": false}'::jsonb)::json
        FROM leaving l
        WHERE l.id = b.id AND l.unread
    """)
    op.execute(
        "DELETE FROM blocks WHERE id IN (SELECT id FROM leaving WHERE NOT unread)"
    )
    op.execute("DROP TABLE leaving")


def downgrade() -> None:
    """The moved rows are not put back: the conversation no longer says them."""
