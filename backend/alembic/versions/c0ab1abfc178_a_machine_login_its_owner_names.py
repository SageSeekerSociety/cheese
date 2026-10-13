"""A name its owner gives a machine's model connection

Revision ID: c0ab1abfc178
Revises: 0cfaea35ed30
Create Date: 2026-10-13

``device_claude_login.name``: what a machine's owner calls the login their own
Claude Code has there, given on 「设置 → 模型连接」 like the name of a connection
their account holds (#2991). The own agents on it are named after it. The
machine never reports it, so nothing it reports overwrites it. Existing rows
have none, so nothing is backfilled.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries

from alembic import op

revision: str = "c0ab1abfc178"
down_revision: str | Sequence[str] | None = "0cfaea35ed30"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with_lock_retries("device_claude_login")
    op.add_column(
        "device_claude_login",
        sa.Column("name", sa.String(64), nullable=True),
    )


def downgrade() -> None:
    # The names go; the own agents keep the names they were last given until
    # their machines next report, and are then named after plan or model again.
    op.drop_column("device_claude_login", "name")
