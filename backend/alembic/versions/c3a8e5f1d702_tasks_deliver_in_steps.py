"""Tasks deliver in steps and hang under the messages they came from

Revision ID: c3a8e5f1d702
Revises: d3a8f6b2c917
Create Date: 2026-10-07

A delivery's card says whether it is its task's last step; every card filed
before this said nothing and was the last.

A task shows under the main-line message it was made from, and a message can
have several. Tasks an AI teammate proposed get the message their proposal's
支线 hangs under, or the main-line message just before a proposal made there;
a task made from a reply in a 支线 gets the message that 支线 hangs under.

The channel's main line no longer carries what happens to a task: the lines
saying a task was created from a message, started, closed, or that its
delivery was filed and accepted move into the task's own conversation, and
the 「更新了这个频道的任务」 lines, which said nothing, go.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c3a8e5f1d702"
down_revision: str | Sequence[str] | None = "d3a8f6b2c917"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: The lines about one delivery, which name its PR.
_DELIVERY_EVENTS = (
    "card_filed",
    "accept_ready",
    "accept_done",
    "accept_stopped",
    "accept_dismissed",
    "card_redescribed",
    "card_voided",
    "force_merged",
    "merge_refused",
    "merge_withheld",
    "pr_closed",
)

#: A line a 支线 hangs under stays where it is.
_NOT_A_THREAD_ROOT = (
    "NOT EXISTS (SELECT 1 FROM threads th WHERE th.root_block_id = b.id)"
)


def _lock(tables: str) -> None:
    """As in b6fcc6362b79: queue for the table a few seconds at a time."""
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
    _lock("accept_cards")
    op.add_column(
        "accept_cards",
        sa.Column(
            "completes_task",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )
    op.create_index(
        "ix_tasks_upgraded_from_block_id", "tasks", ["upgraded_from_block_id"]
    )

    # Where each proposed task came from.
    op.execute("""
        UPDATE tasks t SET upgraded_from_block_id = th.root_block_id
        FROM task_proposals p JOIN threads th ON th.id = p.conversation_id
        WHERE p.task_id = t.id AND t.upgraded_from_block_id IS NULL
    """)
    op.execute("""
        UPDATE tasks t SET upgraded_from_block_id = (
            SELECT b.id FROM blocks b
            WHERE b.conversation_id = p.conversation_id
              AND b.kind = 'message'
              AND b.created_at <= p.created_at
            ORDER BY b.created_at DESC
            LIMIT 1
        )
        FROM task_proposals p
        WHERE p.task_id = t.id
          AND t.upgraded_from_block_id IS NULL
          AND p.conversation_id = t.room_id
    """)
    # A reply in a 支线 hangs its task under the 支线's message.
    op.execute("""
        UPDATE tasks t SET upgraded_from_block_id = th.root_block_id
        FROM blocks b JOIN threads th ON th.id = b.conversation_id
        WHERE b.id = t.upgraded_from_block_id
    """)
    op.execute("UPDATE task_titles SET reason = 'teammate' WHERE reason = 'proposal'")

    # A task's lifecycle lines, into the task.
    op.execute(f"""
        UPDATE blocks b SET conversation_id = t.id
        FROM tasks t
        WHERE b.conversation_id = t.room_id
          AND b.kind = 'event'
          AND b.meta->>'task_id' = t.id::text
          AND (
              b.meta->>'action' IN ('task_started', 'task_closed')
              OR (
                  b.meta->>'action' = 'task_created'
                  AND t.upgraded_from_block_id IS NOT NULL
              )
          )
          AND {_NOT_A_THREAD_ROOT}
    """)
    # A delivery's lines, into the task the PR they name belongs to.
    events = ", ".join(f"'{e}'" for e in _DELIVERY_EVENTS)
    op.execute(f"""
        UPDATE blocks b SET conversation_id = m.task_id
        FROM (
            SELECT DISTINCT topic_id, pr_number, task_id
            FROM accept_cards
            WHERE task_id IS NOT NULL AND pr_number IS NOT NULL
        ) m
        WHERE b.conversation_id = m.topic_id
          AND b.kind = 'event'
          AND b.meta->>'event_type' IN ({events})
          AND b.meta->'i18n'->'content'->'params'->>'pr' = m.pr_number::text
          AND {_NOT_A_THREAD_ROOT}
    """)
    op.execute(f"""
        DELETE FROM blocks b
        WHERE b.kind = 'event'
          AND b.meta->>'action' = 'topics'
          AND {_NOT_A_THREAD_ROOT}
    """)


def downgrade() -> None:
    # The moved and deleted lines stay where they are: nothing recorded where
    # each one was, and a line in its task reads correctly under either code.
    op.drop_index("ix_tasks_upgraded_from_block_id", table_name="tasks")
    op.drop_column("accept_cards", "completes_task")
