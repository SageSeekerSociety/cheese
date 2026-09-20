"""Record explicitly declared reporters and code contributors on tasks."""

import sqlalchemy as sa

from alembic import op

revision = "e8b42a731c90"
down_revision = "d5a20c8e4b13"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("reporter_handle", sa.String(64), nullable=True))
    op.add_column(
        "tasks",
        sa.Column(
            "contributor_handles", sa.JSON(), nullable=False, server_default="[]"
        ),
    )


def downgrade() -> None:
    op.drop_column("tasks", "contributor_handles")
    op.drop_column("tasks", "reporter_handle")
