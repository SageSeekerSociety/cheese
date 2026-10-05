"""Where a row belongs is one conversation id

Revision ID: b6fcc6362b79
Revises: e7c41a9b2d58
Create Date: 2026-10-05

A conversation is a room or a task, registered in ``conversations`` under the
room's or the task's own id (f7985445d2bf). The rows that belong to a
conversation said which one with two columns: ``topic_id`` for the room and a
nullable ``task_id`` for the task, NULL meaning the room's own line. Each pair
becomes one ``conversation_id``: the task's id when the row was a task's, the
room's otherwise.

- **``blocks``, ``agent_turns``, ``resource_usage``, ``topic_progress``,
  ``webhook_tokens``** point at ``conversations`` and go with it. Their indexes
  are rebuilt on the new column; the pairs of partial indexes that split a room's
  line from its tasks' become one.
- **``deliveries``, ``local_fs_access``** hold it bare, as they held the pair.
- **``native_inputs``, ``timed_deliveries``** already held a conversation id
  under the name ``topic_id``; the column is renamed.
- **``agent_sessions.topic_id``**, the room a session works in, is dropped: it
  is the room of ``conversation_id``. The device connection owner stopped reading
  it in #2798.
- The project search index over ``blocks`` filters by ``conversation_id``.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b6fcc6362b79"
down_revision: str | Sequence[str] | None = "e7c41a9b2d58"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: The tables whose pair becomes a column pointing at ``conversations``, and
#: whether a row may belong to none.
_REGISTERED = (
    ("blocks", False),
    ("agent_turns", False),
    ("resource_usage", True),
    ("topic_progress", False),
    ("webhook_tokens", False),
)
_BARE = ("deliveries", "local_fs_access")
_RENAMED = ("native_inputs", "timed_deliveries")

#: As in 2d2fc3a8ce36: the block kinds the project search reads.
SEARCHED_KINDS = ("message", "doc", "doc_node", "comment", "decision", "weekly")


def _lock_all(tables: str) -> None:
    """As in 4383bf20b465: every table at once without waiting, or none, and
    try again shortly — never holding some while waiting on the rest."""
    op.execute(f"""
        DO $$
        DECLARE attempts integer := 0;
        BEGIN
            LOOP
                BEGIN
                    LOCK TABLE {tables} IN ACCESS EXCLUSIVE MODE NOWAIT;
                    EXIT;
                EXCEPTION WHEN lock_not_available THEN
                    attempts := attempts + 1;
                    IF attempts >= 1200 THEN
                        RAISE;
                    END IF;
                    PERFORM pg_sleep(0.05);
                END;
            END LOOP;
        END
        $$
    """)


def _fold(table: str) -> None:
    op.add_column(table, sa.Column("conversation_id", sa.Uuid(), nullable=True))
    op.execute(
        f"UPDATE {table} SET conversation_id = COALESCE(task_id, topic_id)"  # noqa: S608
    )
    op.drop_column(table, "task_id")
    op.drop_column(table, "topic_id")


def upgrade() -> None:
    # Dropping ``topic_id`` and ``task_id`` drops their foreign keys, which
    # locks ``topics`` and ``tasks`` too: taken mid-way, behind a request that
    # holds one of them and wants a table locked here, it deadlocks.
    _lock_all(
        ", ".join(
            (
                *(table for table, _ in _REGISTERED),
                *_BARE,
                *_RENAMED,
                "agent_sessions",
                "conversations",
                "topics",
                "tasks",
            )
        )
    )

    for table, nullable in _REGISTERED:
        _fold(table)
        if not nullable:
            op.alter_column(table, "conversation_id", nullable=False)
        op.create_foreign_key(
            f"fk_{table}_conversation_id",
            table,
            "conversations",
            ["conversation_id"],
            ["id"],
            ondelete="CASCADE",
        )
    for table in _BARE:
        _fold(table)
    for table in _RENAMED:
        op.alter_column(table, "topic_id", new_column_name="conversation_id")
    op.drop_column("agent_sessions", "topic_id")

    op.create_index(
        "ix_blocks_conversation_created_at", "blocks", ["conversation_id", "created_at"]
    )
    op.create_index(
        "ix_blocks_conversation_kind_created",
        "blocks",
        ["conversation_id", "kind", "created_at"],
        postgresql_include=["author"],
    )
    op.create_index(
        "ix_blocks_cloud_provisioning",
        "blocks",
        ["conversation_id", "created_at", "id"],
        postgresql_where=sa.text("(meta ->> 'event_type') = 'cloud_provisioning'"),
    )
    op.create_index(
        "ix_blocks_questions",
        "blocks",
        ["conversation_id", sa.text("created_at DESC")],
        postgresql_where=sa.text(
            "kind = 'message' AND (meta ->> 'options') IS NOT NULL"
        ),
    )
    op.create_index(
        "ix_blocks_machine_events",
        "blocks",
        ["conversation_id", "created_at"],
        postgresql_where=sa.text(
            "(meta ->> 'event_type') IN ('machine_provisioning', 'device_waiting',"
            " 'sandbox_rebuilt', 'environment_repaired')"
        ),
    )
    op.create_index(
        "ix_blocks_failed_turns",
        "blocks",
        ["conversation_id", "created_at"],
        postgresql_where=sa.text(
            "(meta ->> 'event_type') IN ('turn_failed', 'platform_error')"
            " AND (meta ->> 'severity') = 'error'"
        ),
    )
    op.create_index(
        "ix_agent_turns_conversation_id", "agent_turns", ["conversation_id"]
    )
    op.create_index(
        "ix_resource_usage_conversation_id", "resource_usage", ["conversation_id"]
    )
    op.create_unique_constraint(
        "topic_progress_conversation_id_key", "topic_progress", ["conversation_id"]
    )
    op.create_unique_constraint(
        "webhook_tokens_conversation_id_key", "webhook_tokens", ["conversation_id"]
    )

    # The search index named `topic_id`, so dropping the column dropped it.
    op.execute(
        "CREATE INDEX ix_blocks_search ON blocks USING bm25"
        " (id, content, conversation_id, kind) WITH (key_field = 'id', text_fields ="
        ' \'{"content": {"tokenizer": {"type": "jieba"}, "record": "position"},'
        ' "content_ngram": {"column": "content", "tokenizer": {"type": "ngram",'
        ' "min_gram": 2, "max_gram": 3, "prefix_only": false}},'
        ' "conversation_id": {"tokenizer": {"type": "keyword"}},'
        ' "kind": {"tokenizer": {"type": "keyword"}}}\')'
        " WHERE kind IN (" + ", ".join(f"'{k}'" for k in SEARCHED_KINDS) + ")"
    )


def downgrade() -> None:
    raise NotImplementedError("A row's room is the room of its conversation.")
