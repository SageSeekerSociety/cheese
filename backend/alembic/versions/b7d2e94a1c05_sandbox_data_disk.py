"""The data disk a pool host keeps its sandboxes' homes on, found at enrollment.

Revision ID: b7d2e94a1c05
Revises: e3b7a19c4d52
"""

import sqlalchemy as sa
from migration_helpers import with_lock_retries

from alembic import op

revision = "b7d2e94a1c05"
down_revision = "e3b7a19c4d52"
branch_labels = None
depends_on = None


def upgrade():
    with_lock_retries("cloud_hosts, warm_machines")
    for table in ("cloud_hosts", "warm_machines"):
        op.add_column(
            table, sa.Column("sandbox_disk_gb", sa.BigInteger(), nullable=True)
        )


def downgrade():
    for table in ("cloud_hosts", "warm_machines"):
        op.drop_column(table, "sandbox_disk_gb")
