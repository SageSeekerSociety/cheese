"""A triage run remembers which reports it handed out

A rule whose spec carries `feedback_batch` gets the oldest reports nobody has
picked up with each firing. Releasing a claim puts a report back at 已收录 as
if it had never been claimed, so the current status can no longer keep a batch
from being handed out again; the rule remembers what it offered instead
(`routine_runs.feedback_offered`).

Runs from before carry `[]`: nothing can say whether they were ever offered, so
those reports may be batched once more. Additive — the previous image neither
reads nor writes the column, and the server default `[]` is right for every run
it creates.

Revision ID: b2f6d18c9a73
Revises: c5e8a1d47b93
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "b2f6d18c9a73"
down_revision: str | Sequence[str] | None = "b1a2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with_lock_retries("routine_runs")
    op.add_column(
        "routine_runs",
        sa.Column(
            "feedback_offered",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("routine_runs", "feedback_offered")
