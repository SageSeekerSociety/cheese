"""The platform names tasks, not channels

Revision ID: 0058abcb379d
Revises: fc3eab9b8847
Create Date: 2026-10-06

A channel is named by whoever creates it, so a room no longer needs the
bookkeeping automatic naming kept on it: `topics.title_source`,
`title_version`, `title_checked_at`, `title_calibrated` and the
`topic_titles` history go. A task opened without a title is the one the
platform names now, so it gains the same three columns and a history of its
own, `task_titles`. A task still unnamed is stored under 「新任务」 rather
than the room placeholder it was opened with, and a project's choice between
automatic and manual naming moves from `settings.topic_naming` to
`settings.task_naming`.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries

from alembic import op

revision: str = "0058abcb379d"
down_revision: str | Sequence[str] | None = "fc3eab9b8847"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with_lock_retries("topics, tasks, topic_titles, projects")
    op.add_column(
        "tasks",
        sa.Column("title_version", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "tasks",
        sa.Column("title_checked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "tasks",
        sa.Column(
            "title_calibrated", sa.Boolean(), server_default="false", nullable=False
        ),
    )
    op.execute("UPDATE tasks SET title = '新任务' WHERE title_source = 'placeholder'")
    op.create_table(
        "task_titles",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.String(length=16), nullable=False),
        sa.Column("by", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_task_titles_task_created", "task_titles", ["task_id", "created_at"]
    )
    op.drop_table("topic_titles")
    op.drop_column("topics", "title_source")
    op.drop_column("topics", "title_version")
    op.drop_column("topics", "title_checked_at")
    op.drop_column("topics", "title_calibrated")
    op.execute("""
        UPDATE projects
        SET settings = (
            (settings::jsonb - 'topic_naming')
            || jsonb_build_object('task_naming', settings::jsonb -> 'topic_naming')
        )::json
        WHERE settings::jsonb ? 'topic_naming'
    """)


def downgrade() -> None:
    raise RuntimeError(
        "A channel's title history and naming state are gone; restore a verified"
        " backup instead."
    )
