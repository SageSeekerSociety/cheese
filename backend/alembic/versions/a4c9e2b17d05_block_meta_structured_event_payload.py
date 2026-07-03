"""block meta: structured event payload (tool/arg/platform)

Event blocks used to bake a display string into `content` at write time, so a
tool missing from the verb table was frozen untranslated forever. `meta` stores
the structured facts ({"tool", "arg", "platform"}) and the UI formats at
display time; `content` remains a human-readable fallback.

Revision ID: a4c9e2b17d05
Revises: e8a1c5d27f31
Create Date: 2026-07-03 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "a4c9e2b17d05"
down_revision: Union[str, Sequence[str], None] = "e8a1c5d27f31"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("blocks", sa.Column("meta", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("blocks", "meta")
