"""A sandbox home's archive records whether its host found it pushed.

A room's cleanup deletes an archive only when it was. Archives written before
this column have no answer and are kept: they may hold the only copy of work
that was never pushed.
"""

import sqlalchemy as sa

from alembic import op

revision = "6d0ce0a4287b"
down_revision = "7d3a9c61e2b4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "cloud_host_homes",
        sa.Column("archive_published", sa.Boolean(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("cloud_host_homes", "archive_published")
