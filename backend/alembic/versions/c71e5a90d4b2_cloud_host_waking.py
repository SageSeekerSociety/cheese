"""cloud_hosts: waking a suspended or stopped host instead of deleting it

The pool gave up on an enrolled host whose connector stayed away, deleting its
homes and the machine, even when MicroCloud had the machine suspended with its
disk intact (2026-10-06 19:04 on dev: machines 1852 and 1902). It now wakes such
a host, and these columns keep that wake across backend restarts: since when it
has been waking, how many resume or start requests it sent, and when it stopped
trying and kept the host for a person to look at.

Revision ID: c71e5a90d4b2
Revises: a4d8e2f61c07
Create Date: 2026-10-07 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c71e5a90d4b2"
down_revision: str | Sequence[str] | None = "a4d8e2f61c07"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
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


def downgrade() -> None:
    op.drop_column("cloud_hosts", "wake_failed_at")
    op.drop_column("cloud_hosts", "wake_requests")
    op.drop_column("cloud_hosts", "waking_since")
