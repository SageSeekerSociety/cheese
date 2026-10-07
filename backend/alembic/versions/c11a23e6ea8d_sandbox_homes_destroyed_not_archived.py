"""Sandbox homes are destroyed when idle, no longer archived

The home archives already in the private bucket are kept 30 days for a person
to fetch by hand, then deleted (`machine/retained_archives.py`): their keys
move to `retained_home_archives`, with the conversation each one's room is told
in, and the home rows that only stood for an archive go; their sessions are
told on their next tool call that they are in a new sandbox, and a room cleanup
that waited on an unpushed archive goes on. The cloud host wake
columns, unmapped by the previous release, are dropped.

Revision ID: c11a23e6ea8d
Revises: 7d3e1c4b9a20
Create Date: 2026-10-07 20:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c11a23e6ea8d"
down_revision: str | Sequence[str] | None = "7d3e1c4b9a20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("cloud_hosts", "wake_failed_at")
    op.drop_column("cloud_hosts", "wake_requests")
    op.drop_column("cloud_hosts", "waking_since")
    op.create_table(
        "retained_home_archives",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("key", sa.Text(), nullable=False, unique=True),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=True),
        sa.Column("size", sa.BigInteger(), nullable=True),
        sa.Column("published", sa.Boolean(), nullable=True),
        sa.Column("delete_after", sa.DateTime(timezone=True), nullable=False),
        sa.Column("told_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_retained_home_archives_conversation_id",
        "retained_home_archives",
        ["conversation_id"],
    )
    op.create_index(
        "ix_retained_home_archives_session_id",
        "retained_home_archives",
        ["session_id"],
    )
    # The conversation is the session's (a task's session works in the task's
    # own), else the room the home was made for.
    op.execute(
        """
        INSERT INTO retained_home_archives (
            id, key, project_id, conversation_id, session_id, size, published,
            delete_after, created_at, updated_at
        )
        SELECT
            h.id, h.archive_key, h.project_id,
            COALESCE(s.conversation_id, h.topic_id), h.session_id,
            h.archive_size, h.archive_published,
            now() + interval '30 days', now(), now()
        FROM cloud_host_homes h
        LEFT JOIN agent_sessions s ON s.id = h.session_id
        WHERE h.archive_key IS NOT NULL
        ON CONFLICT (key) DO NOTHING
        """
    )
    # Each session whose home goes is told on its next tool call that it is
    # in a new sandbox (``sandbox_wait.LOST_KEY``), as after an idle one.
    op.execute(
        """
        UPDATE agent_sessions s
        SET execution_request = (
            COALESCE(s.execution_request::jsonb, '{}'::jsonb)
            || '{"sandbox_lost": true}'::jsonb
        )::json
        FROM cloud_host_homes h
        WHERE h.session_id = s.id
          AND h.archive_key IS NOT NULL
          AND h.left_at IS NULL
        """
    )
    op.execute("DELETE FROM cloud_host_homes WHERE archive_key IS NOT NULL")
    # A room cleanup that was waiting on an unpushed archive goes on now.
    op.execute(
        "UPDATE room_cleanups SET state = 'pending', last_error = NULL,"
        " failures = 0, due_at = now() WHERE state = 'kept'"
    )


def downgrade() -> None:
    op.drop_index(
        "ix_retained_home_archives_session_id", table_name="retained_home_archives"
    )
    op.drop_index(
        "ix_retained_home_archives_conversation_id",
        table_name="retained_home_archives",
    )
    op.drop_table("retained_home_archives")
    op.add_column(
        "cloud_hosts",
        sa.Column("waking_since", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "cloud_hosts",
        sa.Column("wake_requests", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "cloud_hosts",
        sa.Column("wake_failed_at", sa.DateTime(timezone=True), nullable=True),
    )
