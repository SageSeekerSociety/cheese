"""sandbox_usages keeps how much swap a sandbox may use

Each reading of a sandbox now also reads its cgroup's `memory.swap.max`:
`cloud_sandbox_swap_mb` while it runs, what freezing gives it while frozen.
The admin page shows the swap it uses against it. NULL where the limit is
unlimited or was never read.

The column is nullable, so the release still serving while the migration runs
reads and writes the table as before.

Revision ID: f3b9d1c7a5e2
Revises: c41a7e9d2b58
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries

from alembic import op

revision: str = "f3b9d1c7a5e2"
down_revision: str | Sequence[str] | None = "c41a7e9d2b58"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

USAGES = "sandbox_usages"


def upgrade() -> None:
    with_lock_retries(USAGES)
    op.add_column(USAGES, sa.Column("swap_max_bytes", sa.BigInteger(), nullable=True))


def downgrade() -> None:
    with_lock_retries(USAGES)
    op.drop_column(USAGES, "swap_max_bytes")
