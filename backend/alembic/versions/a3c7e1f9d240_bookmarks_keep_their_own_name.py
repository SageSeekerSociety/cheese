"""Bookmarks keep their own name

Revision ID: a3c7e1f9d240
Revises: d4f8b2a6e931
Create Date: 2026-10-22

A bookmark's name: read off its message when it is made, then refined by the
model or typed by a person. Nullable — a message with no words has no name, and
rows made before this column fall back to the message's first line on screen.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries

from alembic import op

revision: str = "a3c7e1f9d240"
down_revision: str | Sequence[str] | None = "e5a9c3d7f142"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with_lock_retries("chapters")
    op.add_column("chapters", sa.Column("title", sa.String(length=200)))


def downgrade() -> None:
    with_lock_retries("chapters")
    op.drop_column("chapters", "title")
