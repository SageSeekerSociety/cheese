"""A notification belongs to a conversation

Revision ID: 9f2b7c14a8e3
Revises: c3a8e5f1d702
Create Date: 2026-10-07

The inbox row said which place it was about with ``topic_id``: a room. A
decision request asked in a task or a 支线 is not about the room it hangs in,
and the receipt written back when someone decides it (`resolve`) went to the
room's own line — the conversation that asked never read the answer.

``notification`` takes the shape every other row that belongs to a conversation
already has (b6fcc6362b79): one ``conversation_id`` pointing at
``conversations``, holding a room's own id, a task's or a 支线's. Every existing
row names a room, which is a conversation of its own, so the same ids carry
forward unchanged.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "9f2b7c14a8e3"
down_revision: str | Sequence[str] | None = "c3a8e5f1d702"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _lock(tables: str) -> None:
    """As in b6fcc6362b79: queue for the tables a few seconds at a time.

    Dropping the old column drops its foreign key, which locks ``topics`` too:
    taken mid-way, behind a request that holds one of them and wants a table
    locked here, it deadlocks.
    """
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
    _lock("notification, topics, conversations")

    op.add_column(
        "notification", sa.Column("conversation_id", sa.Uuid(), nullable=True)
    )
    op.execute("UPDATE notification SET conversation_id = topic_id")
    # 旧列一走，架在它上面的那个索引跟着走（和 b6fcc6362b79 的 `_fold` 一样）。
    op.drop_column("notification", "topic_id")
    op.create_foreign_key(
        "fk_notification_conversation_id",
        "notification",
        "conversations",
        ["conversation_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "idx_notification_conversation_recipient",
        "notification",
        ["conversation_id", "recipient_handle"],
    )


def downgrade() -> None:
    raise NotImplementedError("A notification is about the conversation it names.")
