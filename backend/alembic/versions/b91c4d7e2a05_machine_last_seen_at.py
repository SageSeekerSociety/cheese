"""project_machines.last_seen_at — when MicroCloud last answered about a machine

A settled machine was never re-checked against MicroCloud, so a machine the
provider had destroyed kept its last known state here forever. `updated_at` is
not usable as the staleness clock: it only moves when a column actually
changes, so a machine reconciled repeatedly with the same answer would look
permanently stale and be re-fetched on every read.

Existing rows get NULL, which reads as "never seen" and schedules them for
reconciliation on the next read — the right outcome for rows written while the
bug was live.

Revision ID: b91c4d7e2a05
Revises: c7f4a1b93e28
Create Date: 2026-08-02
"""

import sqlalchemy as sa

from alembic import op

revision = "b91c4d7e2a05"
down_revision = "c7f4a1b93e28"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "project_machines",
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("project_machines", "last_seen_at")
