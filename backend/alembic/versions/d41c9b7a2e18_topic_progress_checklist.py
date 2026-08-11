"""topics.progress: the task's checklist, kept off the machine

进度层 (#187): the working checklist lived only in Claude Code's session file
inside the topic's container, so it died with the machine and was freed at
accept — "换了机器不知道做到哪". A `task` was already documented as carrying the
branch, the accept card and the progress; the first two had a column, this is
the third.

Nullable with no default: a topic that never ran keeps NULL, and no backfill is
possible (the old checklists were never captured anywhere).

Revision ID: d41c9b7a2e18
Revises: b5045bf862fe
Create Date: 2026-08-10 16:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d41c9b7a2e18"
down_revision: str | Sequence[str] | None = "b8e1d4c70a92"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "topics",
        sa.Column("progress", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("topics", "progress")
