"""sandbox_homes records when its sandbox was frozen

An idle cloud sandbox is frozen instead of destroyed: its processes are
stopped and it is not billed, but its home and its work wait on the host for
the session's next tool call. `frozen_at` is when that happened; NULL while
the sandbox runs, and NULL again once it is woken or destroyed. Frozen
sandboxes are left out of the compute meter.

The column is nullable, so the release still serving while the migration runs
reads and writes the table as before (it simply never fills it).

Revision ID: a3e6c1f9d2b7
Revises: b7c1e9d2a4f5
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries

from alembic import op

revision: str = "a3e6c1f9d2b7"
down_revision: str | Sequence[str] | None = "b7c1e9d2a4f5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

HOMES = "sandbox_homes"


def upgrade() -> None:
    with_lock_retries(HOMES)
    op.add_column(
        HOMES, sa.Column("frozen_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    with_lock_retries(HOMES)
    op.drop_column(HOMES, "frozen_at")
