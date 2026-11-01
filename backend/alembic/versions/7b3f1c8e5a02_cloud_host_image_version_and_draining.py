"""cloud_hosts: what a host was made of, and when it is being rebuilt

The pool now rolls hosts, so a fleet takes a new enrollment script, template or
size without an outage: ``image_version`` is one digest of what a host enrolled
with (`enrollment.current_host_image_version`), and ``draining_since`` marks the
host being rebuilt — it takes no new sandbox and is released once its last one
is gone. Both nullable: a host from before this migration recorded no version,
and the sweep treats that as out of date, which is what rolls the existing
fleet onto the new one, one host at a time.

Revision ID: 7b3f1c8e5a02
Revises: c1ddf6d2257f
Create Date: 2026-11-01 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries

from alembic import op

revision: str = "7b3f1c8e5a02"
down_revision: str | Sequence[str] | None = 'c6e54428528a'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The live backend writes this table every pool sweep, so the DDL takes its
    # lock through the retry helper.
    with_lock_retries("cloud_hosts")
    op.add_column(
        "cloud_hosts",
        sa.Column("image_version", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "cloud_hosts",
        sa.Column("draining_since", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    with_lock_retries("cloud_hosts")
    op.drop_column("cloud_hosts", "draining_since")
    op.drop_column("cloud_hosts", "image_version")
