"""cloud_hosts.disk_full_since: a host whose disk is full takes no sandbox

Revision ID: b29905fee2b7
Revises: 70c713b50151
Create Date: 2026-10-14

Since when the disk guard has found a host's disk below its floor, in bytes or
in inodes; NULL while it has room. Nullable, no backfill: every host starts
with room until its first reading.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries

from alembic import op

revision: str = "b29905fee2b7"
down_revision: str | Sequence[str] | None = "70c713b50151"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with_lock_retries("cloud_hosts")
    op.add_column(
        "cloud_hosts",
        sa.Column("disk_full_since", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("cloud_hosts", "disk_full_since")
