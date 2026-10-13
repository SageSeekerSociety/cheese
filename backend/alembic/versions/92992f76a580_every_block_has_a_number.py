"""Every block has a number

Revision ID: 92992f76a580
Revises: 052f77ef6c92
Create Date: 2026-10-09

`5f1b7d54bffa` numbered the blocks stored before the counter and the trigger
numbers every one since, the running release's included, so from here
`blocks.seq` is NOT NULL. A validated CHECK first, so `SET NOT NULL` does not
read the table under its lock; the check is validated in its own statement,
outside the lock, where reading 500k rows holds up no one.
"""

from collections.abc import Sequence

from migration_helpers import with_lock_retries

from alembic import op

revision: str = "92992f76a580"
down_revision: str | Sequence[str] | None = "052f77ef6c92"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CHECK = "ck_blocks_seq"


def upgrade() -> None:
    with_lock_retries("blocks")
    # Left by a run that stopped after committing it.
    op.execute(f"ALTER TABLE blocks DROP CONSTRAINT IF EXISTS {_CHECK}")
    op.execute(
        f"ALTER TABLE blocks ADD CONSTRAINT {_CHECK} CHECK (seq IS NOT NULL) NOT VALID"
    )
    # Entering the block commits the line above and lets go of the lock.
    with op.get_context().autocommit_block():
        op.execute(f"ALTER TABLE blocks VALIDATE CONSTRAINT {_CHECK}")
    with_lock_retries("blocks")
    # migration-safety: allow alter-column-existing — the validated CHECK above proves it, so SET NOT NULL does not scan
    op.alter_column("blocks", "seq", nullable=False)
    op.execute(f"ALTER TABLE blocks DROP CONSTRAINT {_CHECK}")


def downgrade() -> None:
    with_lock_retries("blocks")
    op.alter_column("blocks", "seq", nullable=True)
