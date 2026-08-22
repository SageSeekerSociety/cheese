"""work is addressed as a task, not as a room

`c4a7e91b2d05` added the two things that make a thread cheaper than a room —
the `tasks` table and `blocks.task_id` — and deliberately pointed nothing at
them. This is the switch it named: every column that used to say "this belongs
to a piece of work" now says WHICH ROOM plus WHICH THREAD, and the `topics`
rows that only ever existed to be a piece of work are deleted.

## Why the room column stays

The obvious shape is to point `agent_sessions.topic_id` at `tasks` instead. It
is wrong: a room has sessions, accept cards, progress and usage of its own, so
the column has to keep answering for both. Every table here therefore keeps
`topic_id` — narrowed to mean THE ROOM, always — and gains a nullable
`task_id`. NULL is the room's own main line. That is the same shape
`blocks.task_id` already has, and having one shape rather than two is worth
more than the row or two it costs.

## Why two partial unique indexes rather than one wider one

`uq_agent_session_topic` is `(topic_id, agent_handle)`. Adding `task_id` to it
looks equivalent and is not: NULL does not compare equal to NULL in a unique
index, so every room's main-line row would stop being mutually exclusive and a
room could quietly grow a second session per agent. The pair of partial indexes
below says the thing that is actually true — one session per agent per room
main-line, one per agent per thread — and each half is enforced.

`topic_progress` and `webhook_tokens` have the same problem one level worse:
their primary key IS `topic_id`, and a primary key cannot hold NULL. Both get a
surrogate key and the same pair of partial indexes.

## The backfill, and why it re-runs `c4a7e91b2d05`'s

That migration warned that from the first split after it ran, new work would
have no `tasks` row. That window is now closed, so its two statements run again
here — imported, not copied, so the SQL that ships is the SQL that is tested.
Everything else in this file is the second half of the same move: rewrite every
reference from the work topic to (room, task), and only then delete the work
topics. Order matters and is enforced by the database: `blocks.topic_id`,
`accept_cards.topic_id` and friends are ON DELETE CASCADE, so deleting a work
topic before its references are rewritten would take the conversation with it.

Revision ID: a9f3c7e21b04
Revises: c4a7e91b2d05
Create Date: 2026-08-22
"""

import importlib.util
from collections.abc import Sequence
from pathlib import Path

import sqlalchemy as sa

from alembic import op

revision: str = "a9f3c7e21b04"
down_revision: str | Sequence[str] | None = "c4a7e91b2d05"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Imported from the migration that owns them rather than pasted: a copy passes
# its own test while the statement that actually runs rots.
def _foundation() -> object:
    path = (
        Path(__file__).resolve().parent / "c4a7e91b2d05_a_task_is_a_thread_in_a_room.py"
    )
    spec = importlib.util.spec_from_file_location("_task_foundation", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Every table that used to point at a work topic. `topic_id` survives on all of
# them meaning THE ROOM; `task_id` is which thread, NULL for the room itself.
_THREADED = (
    "agent_sessions",
    "agent_turns",
    "accept_cards",
    "conclusion_cards",
    "resource_usage",
    "topic_progress",
    "webhook_tokens",
)

# Rows whose only reason to exist was that a piece of work was a room. A task
# has one owner (`tasks.owner_handle`, already backfilled from exactly these
# rows) and no unread cursor at all, so these do not move — they go.
_ROOM_ONLY = ("topic_memberships", "topic_read_states")


def _add_task_id(table: str) -> None:
    op.add_column(table, sa.Column("task_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        f"fk_{table}_task_id",
        table,
        "tasks",
        ["task_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(f"ix_{table}_task_id", table, ["task_id"])


def _pair_of_partial_uniques(table: str, *cols: str) -> None:
    """One row per (room main-line, *cols*) AND one per (thread, *cols*).

    Written as two partial indexes because the single wider index everyone
    reaches for first does not hold: `task_id` is NULL on every room row, and
    NULL is not equal to NULL, so the room half would not be unique at all.
    """
    op.create_index(
        f"uq_{table}_room",
        table,
        ["topic_id", *cols],
        unique=True,
        postgresql_where=sa.text("task_id IS NULL"),
    )
    op.create_index(
        f"uq_{table}_thread",
        table,
        ["task_id", *cols],
        unique=True,
        postgresql_where=sa.text("task_id IS NOT NULL"),
    )


def _surrogate_key(table: str) -> None:
    """Give *table* its own id so `topic_id` can stop being the primary key.

    A primary key cannot be NULL, and `task_id` has to be NULL on every room
    row — so a composite key over the pair is not available and the identity of
    the row has to come from somewhere else.
    """
    op.add_column(
        table,
        sa.Column(
            "id", sa.Uuid(), nullable=False, server_default=sa.text("gen_random_uuid()")
        ),
    )
    op.drop_constraint(f"{table}_pkey", table, type_="primary")
    op.create_primary_key(f"{table}_pkey", table, ["id"])
    op.create_index(f"ix_{table}_topic_id", table, ["topic_id"])


def upgrade() -> None:
    for table in _THREADED:
        _add_task_id(table)

    # `topic_progress` and `webhook_tokens` were keyed BY the topic.
    _surrogate_key("topic_progress")
    _surrogate_key("webhook_tokens")

    op.drop_constraint("uq_agent_session_topic", "agent_sessions", type_="unique")
    _pair_of_partial_uniques("agent_sessions", "agent_handle")
    _pair_of_partial_uniques("topic_progress")
    _pair_of_partial_uniques("webhook_tokens")

    foundation = _foundation()
    bind = op.get_bind()

    # 1. Close the window `c4a7e91b2d05` opened: every split since it ran made a
    #    work topic with no `tasks` row. ON CONFLICT DO NOTHING because the rows
    #    it already landed are still there and still correct.
    bind.execute(
        sa.text(foundation.BACKFILL_TASKS + " ON CONFLICT (id) DO NOTHING")  # type: ignore[attr-defined]
    )
    bind.execute(sa.text(foundation.BACKFILL_BLOCKS))  # type: ignore[attr-defined]

    # 2. Every reference to a work topic becomes (room, task). `task_id` first,
    #    because the room is read back THROUGH it.
    for table in _THREADED:
        bind.execute(
            sa.text(
                f"UPDATE {table} SET task_id = topic_id "  # noqa: S608 — fixed names
                "WHERE topic_id IN (SELECT id FROM tasks)"
            )
        )
        bind.execute(
            sa.text(
                f"UPDATE {table} SET topic_id = t.room_id "  # noqa: S608 — fixed names
                "FROM tasks t WHERE t.id = "
                f"{table}.task_id"
            )
        )
    bind.execute(
        sa.text(
            "UPDATE blocks SET topic_id = t.room_id FROM tasks t "
            "WHERE t.id = blocks.task_id"
        )
    )
    # A conclusion flows FROM a thread TO its room. Both ends were topics.
    bind.execute(
        sa.text(
            "UPDATE conclusion_cards SET receiver_topic_id = t.room_id "
            "FROM tasks t WHERE t.id = conclusion_cards.receiver_topic_id"
        )
    )
    # An alert about a piece of work belongs in the room it hangs in — there is
    # nowhere else it could be read.
    bind.execute(
        sa.text(
            "UPDATE alerts SET topic_id = t.room_id FROM tasks t "
            "WHERE t.id = alerts.topic_id"
        )
    )
    # A block dispatched INTO work now points at the thread, not a topic row
    # that is about to stop existing.
    bind.execute(
        sa.text(
            "UPDATE blocks SET upgraded_to_topic_id = NULL "
            "WHERE upgraded_to_topic_id IN (SELECT id FROM tasks)"
        )
    )

    # 3. The roster and the unread cursor were the costs work paid for being a
    #    room. Nothing inherits them.
    for table in _ROOM_ONLY:
        bind.execute(
            sa.text(
                f"DELETE FROM {table} WHERE topic_id IN (SELECT id FROM tasks)"  # noqa: S608
            )
        )

    # 4. Only now: the work topics themselves. Nested work (a task whose parent
    #    was another task) goes in the same statement, so the self-referential
    #    CASCADE has nothing left to reach.
    bind.execute(sa.text("DELETE FROM topics WHERE kind IN ('task', 'subtopic')"))

    # 5. Say what happened. `strays` and `orphans` must both be 0; the other
    #    numbers are there so a human can check the map by hand.
    row = bind.execute(sa.text(RECONCILE)).mappings().one()
    print(f"[a9f3c7e21b04] {dict(row)}")  # noqa: T201 — migration output is the receipt


# Both halves of the map, counted after the fact.
#
# `keyed_blocks` is NOT compared against a count of work topics any more — that
# was `c4a7e91b2d05`'s check and it stops being meaningful the moment those rows
# are deleted. What has to hold now is that every keyed block's task exists and
# sits in the room the block claims.
RECONCILE = """
SELECT
    (SELECT count(*) FROM tasks) AS task_rows,
    (SELECT count(*) FROM topics WHERE kind IN ('task', 'subtopic'))
        AS work_topics_left,
    (SELECT count(*) FROM blocks WHERE task_id IS NOT NULL) AS keyed_blocks,
    (SELECT count(*) FROM tasks t
      WHERE NOT EXISTS (SELECT 1 FROM topics r WHERE r.id = t.room_id)) AS strays,
    (SELECT count(*) FROM blocks b
       JOIN tasks t ON t.id = b.task_id
      WHERE b.topic_id <> t.room_id) AS orphans
"""


def downgrade() -> None:
    """Undo the schema. The deleted work topics are NOT recreated.

    A downgrade restores the shape, not the history: the rows this dropped were
    duplicated into `tasks` before they went, and inventing `topics` rows back
    out of them would produce a tree that never existed (nested work was
    re-parented to its room on the way in). Anything that needs those rows needs
    a restore, not a downgrade — and saying so here is better than a downgrade
    that appears to work.
    """
    op.drop_index("uq_webhook_tokens_thread", table_name="webhook_tokens")
    op.drop_index("uq_webhook_tokens_room", table_name="webhook_tokens")
    op.drop_index("uq_topic_progress_thread", table_name="topic_progress")
    op.drop_index("uq_topic_progress_room", table_name="topic_progress")
    op.drop_index("uq_agent_sessions_thread", table_name="agent_sessions")
    op.drop_index("uq_agent_sessions_room", table_name="agent_sessions")
    op.create_unique_constraint(
        "uq_agent_session_topic", "agent_sessions", ["topic_id", "agent_handle"]
    )

    for table in ("topic_progress", "webhook_tokens"):
        op.drop_index(f"ix_{table}_topic_id", table_name=table)
        op.drop_constraint(f"{table}_pkey", table, type_="primary")
        op.drop_column(table, "id")
        op.create_primary_key(f"{table}_pkey", table, ["topic_id"])

    for table in _THREADED:
        op.drop_index(f"ix_{table}_task_id", table_name=table)
        op.drop_constraint(f"fk_{table}_task_id", table, type_="foreignkey")
        op.drop_column(table, "task_id")
