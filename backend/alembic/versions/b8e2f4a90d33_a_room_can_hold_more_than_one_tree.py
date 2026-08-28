"""a room can hold more than one tree

一棵树 = 一个分支 = 一个 PR = 一批活。A room used to be exactly one of each,
which made two unrelated things impossible at once:

- while a PR was in flight the room could not take new work, because there was
  nowhere to put it that was not the PR's own tree;
- a piece of work could not deliver on its own, because its commits had to be
  merged into the room's single branch first (`fold_into_room`).

The fix is the same for both: give the ROOM more than one tree. A tree holds a
batch of work, opens one PR, and is sealed while that PR flies; the next batch
starts a new tree and the room keeps working. Many tasks share one tree — that
is the normal case, not an edge one.

## Why a task loses its branch

A task now works in its tree, alongside its siblings. It therefore has no
branch of its own, and `tasks.branch_name` goes. Everything that existed to
carry a task's commits back into the room's branch — the fork-point marker, the
merge, the conflict/deferred/sweep machinery around it — becomes unreachable in
the same change, because there is nothing left to merge: the commits are
already on the tree the PR is open on.

## Why the first tree inherits its room's id

`branch_for_place` derived a branch name from an id (`topic/<hex8>`), and the
worktree directory, the jj workspace name, the container workdir and the tmux
session name all derive from that same id. Giving each room's FIRST tree the
room's own id makes every one of those names byte-for-byte what it already is,
so no branch is renamed, no worktree is moved, and no live agent session is
retired. It is the same trick `c4a7e91b2d05` used to let a task inherit the id
of the topic row it replaced.

The two id spaces cannot collide: a tree's id is a live room's, and a task's is
a deleted work-topic's.

Revision ID: b8e2f4a90d33
Revises: a9f3c7e21b04
Create Date: 2026-08-26
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b8e2f4a90d33"
down_revision: str | Sequence[str] | None = "a9f3c7e21b04"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "work_trees",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "project_id",
            sa.Uuid(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "room_id",
            sa.Uuid(),
            sa.ForeignKey("topics.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "status", sa.String(length=16), nullable=False, server_default="open"
        ),
        sa.Column("sealed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("merged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_work_trees_room_id", "work_trees", ["room_id"])
    op.create_index("ix_work_trees_project_id", "work_trees", ["project_id"])
    # A room takes new work into exactly one tree. Two open trees would make
    # "which tree does this task join" an unanswerable question, and the answer
    # has to exist before a task can be created — so the database holds it
    # rather than a convention.
    op.create_index(
        "uq_work_trees_one_open_per_room",
        "work_trees",
        ["room_id"],
        unique=True,
        postgresql_where=sa.text("status = 'open'"),
    )

    # Every existing room gets its tree, carrying the room's own id so nothing
    # on disk is renamed. `kind` is the room test: root and topic are both
    # rooms; work does not live in `topics` any more.
    op.execute(
        """
        INSERT INTO work_trees (id, project_id, room_id, status, created_at, updated_at)
        SELECT t.id, t.project_id, t.id, 'open', now(), now()
        FROM topics t
        WHERE t.kind IN ('root', 'topic')
        """
    )

    op.add_column(
        "tasks",
        sa.Column(
            "tree_id",
            sa.Uuid(),
            sa.ForeignKey("work_trees.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.execute("UPDATE tasks SET tree_id = room_id WHERE tree_id IS NULL")
    # Only now can it be required: a task without a tree has nowhere to write.
    op.alter_column("tasks", "tree_id", nullable=False)
    op.create_index("ix_tasks_tree_id", "tasks", ["tree_id"])

    # A task works in its tree, with its siblings. It has no branch to name.
    op.drop_column("tasks", "branch_name")
    # `topics.branch_name` never had a writer anywhere in the app — every row
    # was NULL for the whole life of the column and `cheese status` reported
    # `branch: null` because of it. A branch belongs to a tree now, so the
    # column is not merely unwritten, it is answering the wrong question.
    op.drop_column("topics", "branch_name")


def downgrade() -> None:
    op.add_column(
        "topics", sa.Column("branch_name", sa.String(length=200), nullable=True)
    )
    op.add_column(
        "tasks", sa.Column("branch_name", sa.String(length=200), nullable=True)
    )
    op.drop_index("ix_tasks_tree_id", table_name="tasks")
    op.drop_column("tasks", "tree_id")
    op.drop_index("uq_work_trees_one_open_per_room", table_name="work_trees")
    op.drop_index("ix_work_trees_project_id", table_name="work_trees")
    op.drop_index("ix_work_trees_room_id", table_name="work_trees")
    op.drop_table("work_trees")
