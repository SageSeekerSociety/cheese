"""When a person's own memory was last tidied

Revision ID: e5a9c3d7f142
Revises: d4f8b2a6e931
Create Date: 2026-10-23

``assistant_rules.memory_tidied_at`` is when the person's 芝士 last tidied
what it remembers of them (``assistant.memory_tidy``): the next tidy waits a
day from it, and taking a tidy moves it, which is also the tidy's lock. NULL
until the first.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries

from alembic import op

revision: str = "e5a9c3d7f142"
down_revision: str | Sequence[str] | None = "d4f8b2a6e931"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with_lock_retries("assistant_rules")
    op.add_column(
        "assistant_rules",
        sa.Column("memory_tidied_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("assistant_rules", "memory_tidied_at")
