"""A project can be archived by its owner, and remembers which rooms went with it

Revision ID: a7c3e91d5f20
Revises: 06b31e24626f
"""

import sqlalchemy as sa

from alembic import op

revision = "a7c3e91d5f20"
down_revision = "06b31e24626f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "projects", sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "topics",
        sa.Column(
            "archived_with_project",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("topics", "archived_with_project")
    op.drop_column("projects", "archived_at")
