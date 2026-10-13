"""room_locks: drop the table

The room's heavy-operation lock is gone (#3560): each session has its own
machine, so there was nothing left for a room-wide lane to serialize. The
previous release stopped mapping the table.

Revision ID: ea1b19de526b
Revises: e81c4b7a2f05
Create Date: 2026-10-13 02:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries

from alembic import op

revision: str = "ea1b19de526b"
down_revision: str | Sequence[str] | None = "e81c4b7a2f05"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with_lock_retries("room_locks, topics, tasks")
    op.drop_table("room_locks")


def downgrade() -> None:
    # The table as d2b6e91c4a77 built it.
    op.create_table(
        "room_locks",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "room_id",
            sa.Uuid(),
            sa.ForeignKey("topics.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column(
            "resource", sa.String(length=1024), nullable=False, server_default=""
        ),
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
    op.create_index("ix_room_locks_expires_at", "room_locks", ["expires_at"])
