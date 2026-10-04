"""assistant_messages.stopped: an answer the person stopped before it was done

The text of such a row is what had been written when it was stopped. Rows
written before this column default to false: none of them was stopped.

Revision ID: cbf982a4af16
Revises: c455bd47d0ac
Create Date: 2026-10-04
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "cbf982a4af16"
down_revision: str | Sequence[str] | None = "c455bd47d0ac"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "assistant_messages",
        sa.Column("stopped", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("assistant_messages", "stopped")
