"""Record each conversation's raw-view launch boundary without rewriting history."""

import sqlalchemy as sa
from migration_helpers import with_lock_retries

from alembic import op

revision = "7a64b5d89c12"
down_revision = "d4f30c9e71a2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with_lock_retries("conversations")
    op.add_column(
        "conversations",
        sa.Column(
            "raw_records_started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    with_lock_retries("conversations")
    op.drop_column("conversations", "raw_records_started_at")
