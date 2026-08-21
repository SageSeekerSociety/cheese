"""a task is a thread in a room, not a room of its own

Work used to be a room: a full `topics` row, with the roster, the unread cursor,
the archive decision and the sidebar line that a room pays for. It was a room
for one reason — `blocks.topic_id` was the only key a conversation could be
grouped by, so the only way to give a piece of work its own thread was to give
it its own room.

This adds the two things that make a thread cheaper than a room: the `tasks`
table, and `blocks.task_id`.

Nothing reads them yet by design. Splitting still creates a `topics` row, so
this migration ADDS a shape without taking one away, and the switch — pointing
split, sessions, accept cards and the frontend at `tasks` — is a separate
change. The consequence to know about: from the first split after this runs,
new work has no `tasks` row, so **whatever performs the switch has to re-run
this backfill in the same change**.

## What the backfill maps, and what it deliberately does not

Every `topics` row with kind `task` or `subtopic` becomes one `tasks` row, and
**it keeps its id**. The mapping is the identity function, which is what makes
the blocks backfill a straight `task_id := topic_id` and the reconciliation a
plain count on both sides — no intermediate table to trust.

`room_id` is NOT `parent_id`. Work nests today (`_child_kind` never checks
whether the parent is itself a task), so a task's parent is sometimes another
task; the design says work does not nest, so the walk goes up to the nearest
actual room. The nesting itself is not lost — a nested task's fork point lives
in the workspace branch-parent marker on disk, keyed by the same id this row
keeps.

`branch_name` is NOT a verbatim column copy. `topics.branch_name` has no writer
anywhere in the app — the branch a workspace actually sits on is derived,
`branch_for_topic(id)` → `topic/<first 8 hex>` — so copying the column would
carry over a NULL for every row. This stores the value the code uses, and keeps
an explicit column value if one was ever set.

`owner_handle` comes from the task's own roster (`topic_memberships`, role
owner), which is what `seed_split` writes and therefore where 唯一的主 already
is.

`status` has two values because two is what the old rows can answer: active or
archived. Delivery is not folded in — `accepted_at` carries it separately, so a
task delivered but still being pushed to stays distinguishable from one closed
with nothing delivered.

Revision ID: c4a7e91b2d05
Revises: b3e1d70c4a92
Create Date: 2026-08-21
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c4a7e91b2d05"
down_revision: str | Sequence[str] | None = "b3e1d70c4a92"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# The two statements below are the backfill, and they are module-level and
# public so the functional test can run THE SHIPPED SQL rather than a copy of
# it — a copy would pass while the migration that actually runs is broken.
#
# One row per work topic, carrying the room it resolves to. The recursive term
# only expands a node that is itself work, so the walk stops at the first room
# and each task yields exactly one row here. The depth guard is a cycle brake:
# `parent_id` is not constrained to be acyclic, and a migration must not hang.
WALK_TO_ROOM = """
WITH RECURSIVE walk(work_id, node_id, node_kind, node_parent, depth) AS (
    SELECT t.id, t.id, t.kind, t.parent_id, 0
      FROM topics t
     WHERE t.kind IN ('task', 'subtopic')
    UNION ALL
    SELECT w.work_id, p.id, p.kind, p.parent_id, w.depth + 1
      FROM walk w
      JOIN topics p ON p.id = w.node_parent
     WHERE w.node_kind IN ('task', 'subtopic')
       AND w.depth < 32
)
SELECT work_id, node_id AS room_id
  FROM walk
 WHERE node_kind NOT IN ('task', 'subtopic')
"""

BACKFILL_TASKS = f"""
INSERT INTO tasks (
    id, project_id, room_id, title, status, owner_handle, created_by,
    agent_instance_id, branch_name, accepted_by, accepted_at, closed_at,
    upgraded_from_block_id, created_at, updated_at
)
SELECT
    t.id,
    t.project_id,
    -- COALESCE, not a plain join: a work row whose ancestry cannot be walked
    -- still has a literal parent, and landing it there beats dropping it. If
    -- even that is NULL the NOT NULL constraint stops the migration, which is
    -- the right noise for a tree with no root.
    COALESCE(r.room_id, t.parent_id),
    t.title,
    CASE WHEN t.status = 'archived' THEN 'closed' ELSE 'open' END,
    (SELECT m.member_handle
       FROM topic_memberships m
      WHERE m.topic_id = t.id AND m.role = 'owner'
      ORDER BY m.created_at
      LIMIT 1),
    t.created_by,
    t.agent_instance_id,
    COALESCE(
        NULLIF(t.branch_name, ''),
        'topic/' || left(replace(t.id::text, '-', ''), 8)
    ),
    t.accepted_by,
    t.accepted_at,
    t.archived_at,
    t.upgraded_from_block_id,
    -- Carried, not stamped with now(): the room's task list is ordered by
    -- created_at, and 219 rows sharing one instant is not an ordering.
    t.created_at,
    t.updated_at
  FROM topics t
  LEFT JOIN ({WALK_TO_ROOM}) r ON r.work_id = t.id
 WHERE t.kind IN ('task', 'subtopic')
"""

# Identity mapping, so this is the whole thing: every block that was in a work
# topic is that task's thread.
BACKFILL_BLOCKS = """
UPDATE blocks
   SET task_id = topic_id
  WHERE topic_id IN (SELECT id FROM tasks)
"""


def upgrade() -> None:
    op.create_table(
        "tasks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("room_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column(
            "status",
            sa.Enum("open", "closed", name="taskstatus", native_enum=False, length=16),
            nullable=False,
        ),
        sa.Column("owner_handle", sa.String(length=64), nullable=True),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("agent_instance_id", sa.Uuid(), nullable=True),
        sa.Column("branch_name", sa.String(length=200), nullable=True),
        sa.Column("accepted_by", sa.String(length=64), nullable=True),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("upgraded_from_block_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="tasks_pkey"),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_tasks_project_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["room_id"], ["topics.id"], name="fk_tasks_room_id", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["agent_instance_id"],
            ["agent_instances.id"],
            name="fk_tasks_agent_instance_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["upgraded_from_block_id"],
            ["blocks.id"],
            name="fk_tasks_upgraded_from_block_id",
            ondelete="SET NULL",
        ),
    )
    op.create_index("ix_tasks_project_id", "tasks", ["project_id"])
    op.create_index("ix_tasks_room_id", "tasks", ["room_id"])
    op.create_index("ix_tasks_agent_instance_id", "tasks", ["agent_instance_id"])
    op.create_index("ix_tasks_room_id_created_at", "tasks", ["room_id", "created_at"])

    op.add_column("blocks", sa.Column("task_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_blocks_task_id", "blocks", "tasks", ["task_id"], ["id"], ondelete="CASCADE"
    )
    # Partial: most blocks are the room's own line and carry no task, and
    # indexing those NULLs would double the index for a reader that never asks.
    op.create_index(
        "ix_blocks_task_id_created_at",
        "blocks",
        ["task_id", "created_at"],
        postgresql_where=sa.text("task_id IS NOT NULL"),
    )

    op.execute(BACKFILL_TASKS)
    op.execute(BACKFILL_BLOCKS)


def downgrade() -> None:
    # Every row here is derived from `topics`, which this migration does not
    # touch — so dropping the table loses nothing that cannot be rebuilt by
    # running upgrade again.
    op.drop_index("ix_blocks_task_id_created_at", table_name="blocks")
    op.drop_constraint("fk_blocks_task_id", "blocks", type_="foreignkey")
    op.drop_column("blocks", "task_id")

    op.drop_index("ix_tasks_room_id_created_at", table_name="tasks")
    op.drop_index("ix_tasks_agent_instance_id", table_name="tasks")
    op.drop_index("ix_tasks_room_id", table_name="tasks")
    op.drop_index("ix_tasks_project_id", table_name="tasks")
    op.drop_table("tasks")
