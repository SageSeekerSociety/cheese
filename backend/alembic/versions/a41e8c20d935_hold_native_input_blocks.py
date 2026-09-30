"""Keep unresolved native inputs out of new prompt assembly.

Revision ID: a41e8c20d935
Revises: f2b6d918a047
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "a41e8c20d935"
down_revision = "f2b6d918a047"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "native_inputs",
        sa.Column(
            "held_block_ids", postgresql.JSONB(), nullable=False, server_default="[]"
        ),
    )
    op.alter_column("native_inputs", "held_block_ids", server_default=None)


def downgrade() -> None:
    op.drop_column("native_inputs", "held_block_ids")
