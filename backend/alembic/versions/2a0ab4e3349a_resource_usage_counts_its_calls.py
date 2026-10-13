"""resource_usage counts its calls

A gateway drain lands each model's spend since the last drain as one row, so
the number of rows says nothing about how many calls the gateway made: the
dashboard's count of calls read a few rows where there were thousands of calls.
`calls` is how many model calls a row covers. Every row written so far keeps 1:
that is right for the metering proxy's rows, and a gateway row's real count was
never recorded and cannot be recovered.

Revision ID: 2a0ab4e3349a
Revises: f175b976a14a
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries

from alembic import op

revision: str = "2a0ab4e3349a"
down_revision: str | Sequence[str] | None = "f175b976a14a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with_lock_retries("resource_usage")
    op.add_column(
        "resource_usage",
        sa.Column("calls", sa.Integer(), nullable=False, server_default="1"),
    )


def downgrade() -> None:
    op.drop_column("resource_usage", "calls")
