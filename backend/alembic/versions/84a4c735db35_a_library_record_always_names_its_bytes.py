"""A library record always names where its bytes are

Revision ID: 84a4c735db35
Revises: c47997681006
Create Date: 2026-10-08

``c47997681006`` filled every empty ``library_files.blob_key``; from here
the column is NOT NULL. A validated CHECK first, so ``SET NOT NULL`` does
not scan the table under its lock.
"""

from collections.abc import Sequence

from migration_helpers import with_lock_retries

from alembic import op

revision: str = "84a4c735db35"
down_revision: str | Sequence[str] | None = "c47997681006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CHECK = "ck_library_files_blob_key"


def upgrade() -> None:
    with_lock_retries("library_files")
    op.execute(
        f"ALTER TABLE library_files ADD CONSTRAINT {_CHECK}"
        " CHECK (blob_key IS NOT NULL) NOT VALID"
    )
    op.execute(f"ALTER TABLE library_files VALIDATE CONSTRAINT {_CHECK}")
    # migration-safety: allow alter-column-existing — the validated CHECK above proves it, so SET NOT NULL does not scan
    op.alter_column("library_files", "blob_key", nullable=False)
    op.execute(f"ALTER TABLE library_files DROP CONSTRAINT {_CHECK}")


def downgrade() -> None:
    op.alter_column("library_files", "blob_key", nullable=True)
