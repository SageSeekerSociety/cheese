"""Retain a confirmed terminal outcome, distinct from clean completion.

A native result that is an error, or that a Stop interrupted, never reaches
``complete_work_inputs`` — so ``completed_at`` stayed NULL and the seat was
blocked forever. Recording the terminal outcome on its own columns keeps that
block accurate without ever claiming completion.

Revision ID: e7b3c2d1a0f4
Revises: d6a32e4b9081
"""

import sqlalchemy as sa

from alembic import op

revision = "e7b3c2d1a0f4"
down_revision = "d6a32e4b9081"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Nullable with no backfill: an existing row simply has no terminal outcome.
    op.add_column(
        "native_inputs",
        sa.Column("terminated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "native_inputs",
        sa.Column("termination", sa.String(length=32), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("native_inputs", "termination")
    op.drop_column("native_inputs", "terminated_at")
