"""a piece of work carries the model it runs on

Revision ID: d3b8f1c72a94
Revises: b9e4c17d0a52
Create Date: 2026-09-21 10:00:00

Two nullable scalar columns on ``tasks``: which model this piece of work runs
on, and at what effort. NULL means the work has no binding of its own and
follows the project default, which is what every row is on the day this runs.

Two columns rather than one JSONB blob: there are exactly two known scalars, and
a blob would buy nothing but the loss of schema validation and of any guard that
could be written against what a piece of work is bound to.

Nothing is backfilled and nothing is dropped, so dev keeps serving through the
window in both directions: the old image does not know these columns exist and
goes on resolving the model off the agent instance, while the new image reads an
empty binding as "follow the project default" — the same answer.

Downgrade drops them. No other table references them.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d3b8f1c72a94"
down_revision: str | Sequence[str] | None = "2895c4967ca4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("model", sa.String(length=128), nullable=True))
    op.add_column("tasks", sa.Column("effort", sa.String(length=32), nullable=True))


def downgrade() -> None:
    op.drop_column("tasks", "effort")
    op.drop_column("tasks", "model")
