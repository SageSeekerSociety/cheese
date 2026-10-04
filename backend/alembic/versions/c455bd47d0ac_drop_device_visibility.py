"""Drop device.visibility: access is chosen per topic, not per machine

What a turn may see is decided where a topic binds to a machine
(`device_topic.visibility`), and nothing in `backend/app` reads or writes the
device-level column any more.

The device connection owner keeps its old image across an app release while
this migration runs; its device reads name their columns
(`domain/device/owner_reads.py`) and never select this one.

The downgrade brings the column back with its last definition: not null,
`isolated` by default. The values it held are not recoverable, and nothing that
could run against the downgraded schema reads them.

Revision ID: c455bd47d0ac
Revises: 761d32f96d84
Create Date: 2026-10-03
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c455bd47d0ac"
down_revision: str | Sequence[str] | None = "761d32f96d84"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("device", "visibility")


def downgrade() -> None:
    op.add_column(
        "device",
        sa.Column(
            "visibility",
            sa.String(length=16),
            nullable=False,
            server_default="isolated",
        ),
    )
