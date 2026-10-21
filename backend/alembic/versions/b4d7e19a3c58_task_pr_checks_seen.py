"""Remember the last PR check state the poll saw on each task."""

import sqlalchemy as sa

from alembic import op

revision = "b4d7e19a3c58"
down_revision = "cf05a4b9127e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("pr_checks_head", sa.String(64), nullable=True))
    op.add_column("tasks", sa.Column("pr_checks_state", sa.String(16), nullable=True))


def downgrade() -> None:
    op.drop_column("tasks", "pr_checks_state")
    op.drop_column("tasks", "pr_checks_head")
