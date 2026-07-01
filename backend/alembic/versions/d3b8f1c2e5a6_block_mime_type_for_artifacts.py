"""block mime_type for artifacts (render-by-type, spec §9.1)

Revision ID: d3b8f1c2e5a6
Revises: c7e1a2f4d9b0
Create Date: 2026-07-01 10:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "d3b8f1c2e5a6"
down_revision: Union[str, Sequence[str], None] = "c7e1a2f4d9b0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("blocks", sa.Column("mime_type", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("blocks", "mime_type")
