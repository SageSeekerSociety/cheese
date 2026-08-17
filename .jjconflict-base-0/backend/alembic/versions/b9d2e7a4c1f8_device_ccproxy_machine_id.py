"""device.ccproxy_machine_id — the per-device ticket's revocation handle (#420)

Every existing device gets NULL: no historical device is claimed as
ccproxy-registered, so deletion keeps costing them nothing to revoke.

Revision ID: b9d2e7a4c1f8
Revises: e6c8a12f4b90
Create Date: 2026-08-16
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b9d2e7a4c1f8"
down_revision: str | Sequence[str] | None = "e6c8a12f4b90"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "device", sa.Column("ccproxy_machine_id", sa.BigInteger(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("device", "ccproxy_machine_id")
