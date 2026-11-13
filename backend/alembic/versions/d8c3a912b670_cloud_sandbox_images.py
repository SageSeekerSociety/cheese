"""Record prepared host images and each sandbox's lifetime image."""

import sqlalchemy as sa
from migration_helpers import with_lock_retries

from alembic import op

revision = "d8c3a912b670"
down_revision = '5a8c1e3f7b20'
branch_labels = None
depends_on = None


def upgrade():
    with_lock_retries("cloud_hosts, warm_machines, sandbox_homes")
    op.add_column(
        "cloud_hosts", sa.Column("sandbox_image", sa.String(255), nullable=True)
    )
    op.add_column(
        "warm_machines", sa.Column("sandbox_image", sa.String(255), nullable=True)
    )
    op.add_column(
        "sandbox_homes", sa.Column("sandbox_image", sa.String(255), nullable=True)
    )


def downgrade():
    op.drop_column("sandbox_homes", "sandbox_image")
    op.drop_column("warm_machines", "sandbox_image")
    op.drop_column("cloud_hosts", "sandbox_image")
