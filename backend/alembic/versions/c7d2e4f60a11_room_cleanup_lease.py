"""A room cleanup is leased to the sweep working on it.

Revision ID: c7d2e4f60a11
Revises: b4c7e2a91d05
Create Date: 2026-09-19 00:20:00

The sweep used a session advisory lock per cleanup, which pinned a pool
connection for the whole of the device work. A lease on the row says the same
thing — one sweep at a time, across processes — without holding a connection,
and expires on its own if the sweep dies.
"""

import sqlalchemy as sa

from alembic import op

revision = "c7d2e4f60a11"
down_revision = "b4c7e2a91d05"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("room_cleanups", sa.Column("lease_until", sa.DateTime(timezone=True)))
    op.add_column("room_cleanups", sa.Column("lease_holder", sa.String(128)))


def downgrade() -> None:
    op.drop_column("room_cleanups", "lease_holder")
    op.drop_column("room_cleanups", "lease_until")
