"""sandbox_homes records when its sandbox was parked

A frozen cloud sandbox past its keep time is parked instead of destroyed: its
processes are stopped and it gives up its host's slot, but its home — the
checkout, the dependencies, what was not pushed — stays on the host's disk
for up to two days, and the session's next tool call starts it again there.
`parked_at` is when that happened; NULL otherwise.

The column is nullable, so the release still serving while the migration runs
reads and writes the table as before (it simply never fills it).

Revision ID: d6f2b8a4c1e7
Revises: e5a7c3d9b1f4
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries

from alembic import op

revision: str = "d6f2b8a4c1e7"
down_revision: str | Sequence[str] | None = "e5a7c3d9b1f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

HOMES = "sandbox_homes"


def upgrade() -> None:
    with_lock_retries(HOMES)
    op.add_column(
        HOMES, sa.Column("parked_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    with_lock_retries(HOMES)
    op.drop_column(HOMES, "parked_at")
