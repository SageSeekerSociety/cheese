"""feedback_timeline.note: why a status step happened

Revision ID: c4f1d8a2e9b7
Revises: 2d2fc3a8ce36

The dev deploy moves a report to `deployed` when the release that fixes it goes
live, with no person behind the step. The note carries which PR did it. Nullable
and without a default: every existing step was taken by a person and has
nothing to say here.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c4f1d8a2e9b7"
down_revision: str | Sequence[str] | None = "2d2fc3a8ce36"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("feedback_timeline", sa.Column("note", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("feedback_timeline", "note")
