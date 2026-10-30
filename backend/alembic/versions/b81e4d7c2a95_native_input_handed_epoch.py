"""Record which process an input was handed to, and when that process ended

An unread input holds its seat until its outcome is known. Whether it can still
be read is a question about the one process holding it, so each input now
records the registration (worker epoch) of the process it was last handed to,
and when the platform recorded that process's exit (its own clock). Both start
empty: an existing row was handed to a process nobody recorded.

Revision ID: b81e4d7c2a95
Revises: c4e8a1f27b93
Create Date: 2026-10-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries

from alembic import op

revision: str = "b81e4d7c2a95"
down_revision: str | Sequence[str] | None = "c4e8a1f27b93"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with_lock_retries("native_inputs")
    op.add_column(
        "native_inputs", sa.Column("handed_epoch", sa.BigInteger(), nullable=True)
    )
    op.add_column(
        "native_inputs",
        sa.Column("receiver_exited_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("native_inputs", "receiver_exited_at")
    op.drop_column("native_inputs", "handed_epoch")
