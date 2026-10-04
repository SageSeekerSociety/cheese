"""A person can mute a room: topic_read_states.notify_level

The read cursor is already the one (topic, person) row there is, so the
person's notification level for that room lives on it too:

- `all` (default): the room's unread messages count toward badges.
- `mute`: they still count in the room's own row, but not toward any total
  (sidebar group badge, desktop badge, tab title).

A string, not a boolean, so a later "mentions only" is a new value rather than
a second column.

Revision ID: 7c3e5a9d1f20
Revises: 41a261d02e9e
Create Date: 2026-10-04
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "7c3e5a9d1f20"
down_revision: str | None = "41a261d02e9e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "topic_read_states",
        sa.Column("notify_level", sa.String(16), nullable=False, server_default="all"),
    )


def downgrade() -> None:
    op.drop_column("topic_read_states", "notify_level")
