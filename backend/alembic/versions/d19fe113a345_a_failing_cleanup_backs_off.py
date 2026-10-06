"""A cleanup that keeps failing for the same reason backs off

Revision ID: d19fe113a345
Revises: 76632669f1f0
Create Date: 2026-10-07

``room_cleanups.failures``: how many attempts in a row have failed for the
reason in ``last_error``. The next attempt waits twice as long each time, an
hour at most (`topic/retire.py`), instead of every sweep, every minute.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d19fe113a345"
down_revision: str | Sequence[str] | None = "76632669f1f0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "room_cleanups",
        sa.Column("failures", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("room_cleanups", "failures")
