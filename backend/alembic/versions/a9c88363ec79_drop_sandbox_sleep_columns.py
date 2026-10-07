"""Drop what sleeping and archived sandbox homes and draining hosts kept

The previous release stopped mapping these: a home's busy hold and archive
fields, and the host draining flag. Every home is on a host now, so its host
column becomes required.

The previous release also marked kept home archives as told when the notice
could not be placed: one for a task's or a 支线's conversation was dropped.
Those are told again.

Revision ID: a9c88363ec79
Revises: c11a23e6ea8d
Create Date: 2026-10-07 22:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a9c88363ec79"
down_revision: str | Sequence[str] | None = "c11a23e6ea8d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

HOME_COLUMNS = (
    "busy_until",
    "archive_key",
    "archive_size",
    "archive_md5",
    "archive_published",
    "archive_failed_at",
    "archive_error",
)


def upgrade() -> None:
    for column in HOME_COLUMNS:
        op.drop_column("cloud_host_homes", column)
    op.execute("DELETE FROM cloud_host_homes WHERE host_id IS NULL")
    op.alter_column("cloud_host_homes", "host_id", nullable=False)
    op.drop_column("cloud_hosts", "draining")
    op.execute(
        """
        UPDATE retained_home_archives r SET told_at = NULL
        WHERE NOT EXISTS (
            SELECT 1 FROM blocks b
            WHERE b.conversation_id = r.conversation_id
              AND b.meta->>'event_type' = 'sandbox_archives_kept'
        )
        """
    )


def downgrade() -> None:
    op.add_column(
        "cloud_hosts",
        sa.Column("draining", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.alter_column("cloud_host_homes", "host_id", nullable=True)
    op.add_column(
        "cloud_host_homes",
        sa.Column("busy_until", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "cloud_host_homes", sa.Column("archive_key", sa.Text(), nullable=True)
    )
    op.add_column(
        "cloud_host_homes", sa.Column("archive_size", sa.BigInteger(), nullable=True)
    )
    op.add_column(
        "cloud_host_homes", sa.Column("archive_md5", sa.String(32), nullable=True)
    )
    op.add_column(
        "cloud_host_homes", sa.Column("archive_published", sa.Boolean(), nullable=True)
    )
    op.add_column(
        "cloud_host_homes",
        sa.Column("archive_failed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "cloud_host_homes", sa.Column("archive_error", sa.Text(), nullable=True)
    )
