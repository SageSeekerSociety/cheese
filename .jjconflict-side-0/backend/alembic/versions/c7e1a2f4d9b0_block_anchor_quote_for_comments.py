"""block anchor_quote for段落评论 (B4)

Revision ID: c7e1a2f4d9b0
Revises: 4f9b64cd1d48
Create Date: 2026-07-01 06:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c7e1a2f4d9b0"
down_revision: str | Sequence[str] | None = "4f9b64cd1d48"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("blocks", sa.Column("anchor_quote", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("blocks", "anchor_quote")
