"""A home's room files are sent by one process at a time

Revision ID: 4b7e2d9c1f30
Revises: e241eeb9ffdb
Create Date: 2026-10-07

Two backends looking at the same home at once (an old and a new one during a
deploy) both packed and sent its files and both inserted its row; the second
insert failed on the unique home key. `looking_since` records which process
has the home while its files are being sent (`agent/device_storage.py`).
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "4b7e2d9c1f30"
down_revision: str | Sequence[str] | None = "e241eeb9ffdb"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "kept_room_files",
        sa.Column("looking_since", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("kept_room_files", "looking_since")
