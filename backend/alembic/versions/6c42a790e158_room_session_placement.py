"""Record the session host separately from the execution device."""

import sqlalchemy as sa

from alembic import op

revision = "6c42a790e158"
down_revision = "d7a419be028c"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("topics", sa.Column("session_placement", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("topics", "session_placement")
