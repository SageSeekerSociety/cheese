"""Retain clean completion independently of acceptance and native echo.

Revision ID: d6a32e4b9081
Revises: c5a841d927be
"""

import sqlalchemy as sa

from alembic import op

revision = "d6a32e4b9081"
down_revision = "c5a841d927be"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Old acceptance/echo facts are not evidence of a successful native result.
    op.add_column(
        "native_inputs",
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("native_inputs", "completed_at")
