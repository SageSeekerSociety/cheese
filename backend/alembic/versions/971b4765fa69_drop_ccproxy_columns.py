"""Drop the ccproxy columns: devices and machines carry no subscription identity

Claude Code sessions run on the platform's session host with that host's own
login, so no device or Cloud machine carries a ccproxy identity or a ccproxy
machine id any more, and nothing in `backend/app` reads or writes these
columns.

The device connection owner keeps its old image across an app release while
this migration runs, so it matters that the owner does not load these tables
as whole ORM models. It does not: its token and execution reads name their
columns (`domain/device/owner_reads.py`), which
`test_owner_reads_survive_a_column_drop.py` holds to by dropping device
columns under device auth and connect.

The downgrade brings the columns back empty. The values are not recoverable,
and nothing that could run against the downgraded schema needs them.

Revision ID: 971b4765fa69
Revises: c5e1a9d3f742
Create Date: 2026-09-24
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "971b4765fa69"
down_revision: str | Sequence[str] | None = "c5e1a9d3f742"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("device", "ccproxy_upstream")
    op.drop_column("device", "ccproxy_machine_id")
    op.drop_column("project_machines", "ccproxy_upstream")
    op.drop_column("warm_machines", "ccproxy_upstream")


def downgrade() -> None:
    op.add_column(
        "warm_machines", sa.Column("ccproxy_upstream", sa.Text(), nullable=True)
    )
    op.add_column(
        "project_machines", sa.Column("ccproxy_upstream", sa.Text(), nullable=True)
    )
    op.add_column(
        "device", sa.Column("ccproxy_machine_id", sa.BigInteger(), nullable=True)
    )
    op.add_column("device", sa.Column("ccproxy_upstream", sa.Text(), nullable=True))
