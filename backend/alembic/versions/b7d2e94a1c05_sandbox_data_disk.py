"""The data disk a pool host keeps its sandboxes' homes on, found at enrollment.

Revision ID: b7d2e94a1c05
Revises: f4b1298ca631
"""

from alembic import op
import sqlalchemy as sa
from migration_helpers import with_lock_retries

revision = "b7d2e94a1c05"
down_revision = "f4b1298ca631"
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
