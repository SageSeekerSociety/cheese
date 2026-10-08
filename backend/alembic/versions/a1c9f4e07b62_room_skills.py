"""A room keeps the skills it switched on

Revision ID: a1c9f4e07b62
Revises: 0c800ff1db2f
Create Date: 2026-10-08

The composer's 技能广场 switches a platform skill on for one room: the turn then
prepends that skill's guidance, so every agent in the conversation follows it
from its first word. Which ones are on is a fact about the room — the names
live on ``topics``.

Nullable, no default: NULL and `[]` both mean "this room runs on the platform's
own guidance only", which is what every room already does before anyone opens
the plaza.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "a1c9f4e07b62"
down_revision: str | Sequence[str] | None = "0c800ff1db2f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with_lock_retries("topics")
    op.add_column("topics", sa.Column("skills", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("topics", "skills")
