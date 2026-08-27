"""who is touching which paths

Threads batched onto one tree write to one working directory. DeepSeek Harness
does the same — `SubagentStartRequest` has no cwd field at all, children inherit
the parent's — and it offers nothing to stop two of them writing the same file:
its answer is that the coordinator arranges non-overlapping work. Its three
non-coupling rules are about provider state, settlement and cleanup; none of
them is about files.

Arranging non-overlapping work is a good answer. Leaving it entirely to whoever
writes the brief is not, because the failure is SILENT: the second write wins,
no tool reports anything, and the loss shows up later as a change that
"mysteriously reverted".

Two tables' worth of answer, and deliberately no more:

`tasks.claimed_paths` — what a piece of work SAYS it will touch. A separate,
mutable column rather than a line in the brief, because a brief is written once
and cannot be changed, while a claim always grows: work reaches a file nobody
predicted. Overlapping directories are a warning (two threads in
`domain/review/` is normal); the same FILE is a refusal.

`room_locks` — the narrow case a claim cannot cover: writing a file WHOLE. An
`Edit` is already safe, because it matches the text it expects to replace and
fails loudly when someone else moved it — that is a compare-and-swap. A `Write`
or a shell `>` has no such check, so it is the one operation that silently
overwrites, and the only one that needs a lock.

`expires_at` is not optional: an agent that dies mid-write never comes back to
unlock, and a lock nobody can release is worse than the overwrite it prevents.

Revision ID: d2b6e91c4a77
Revises: c1a5d73f8b20
Create Date: 2026-08-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "d2b6e91c4a77"
down_revision: str | Sequence[str] | None = "c1a5d73f8b20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column(
            "claimed_paths",
            postgresql.ARRAY(sa.String()),
            nullable=False,
            server_default="{}",
        ),
    )
    op.create_table(
        "room_locks",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "room_id",
            sa.Uuid(),
            sa.ForeignKey("topics.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # `file` guards one whole-file overwrite; `heavy` is the room's single
        # lane for the things that fight over the machine rather than over the
        # tree — a test run, a dependency install, a dev server's port.
        sa.Column("kind", sa.String(length=16), nullable=False),
        # The path, for a `file` lock. Empty for `heavy`, which is one lane per
        # room rather than one per resource — an empty string rather than NULL
        # so the unique index below actually constrains it (NULL never equals
        # NULL, so a nullable column would let a room hold any number of them).
        sa.Column(
            "resource", sa.String(length=1024), nullable=False, server_default=""
        ),
        # NULL = the room's own line holds it. The room writes to the same tree
        # as its threads and can overwrite their files exactly as they can
        # overwrite each other's, so it is not exempt.
        sa.Column(
            "holder_task_id",
            sa.Uuid(),
            sa.ForeignKey("tasks.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "acquired_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
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
    op.create_index(
        "uq_room_locks_resource",
        "room_locks",
        ["room_id", "kind", "resource"],
        unique=True,
    )
    # The sweep asks one question — what has expired — across every room.
    op.create_index("ix_room_locks_expires_at", "room_locks", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_room_locks_expires_at", table_name="room_locks")
    op.drop_index("uq_room_locks_resource", table_name="room_locks")
    op.drop_table("room_locks")
    op.drop_column("tasks", "claimed_paths")
