"""Distinguish admitted input work from its native execution owner.

Revision ID: c5a841d927be
Revises: b913e62704ac
"""

import sqlalchemy as sa

from alembic import op

revision = "c5a841d927be"
down_revision = "b913e62704ac"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "native_inputs", sa.Column("execution_work_id", sa.Uuid(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("native_inputs", "execution_work_id")
