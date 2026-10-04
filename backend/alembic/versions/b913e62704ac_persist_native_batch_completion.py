"""Preserve completed block ownership after later consumption markers change.

Revision ID: b913e62704ac
Revises: a41e8c20d935
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "b913e62704ac"
down_revision = "a41e8c20d935"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "native_inputs",
        sa.Column(
            "released_block_ids",
            postgresql.JSONB(),
            nullable=False,
            server_default="[]",
        ),
    )
    op.alter_column("native_inputs", "released_block_ids", server_default=None)


def downgrade() -> None:
    op.drop_column("native_inputs", "released_block_ids")
