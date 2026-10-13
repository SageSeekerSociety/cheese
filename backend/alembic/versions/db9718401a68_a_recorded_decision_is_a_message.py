"""A recorded decision is a message

Revision ID: db9718401a68
Revises: e5c1a9f7b204
Create Date: 2026-10-01 13:00:00

Agents no longer record decisions as their own kind of block: a decision is
something said in the conversation. The rows written before that keep their
author, room, time and text and become messages, so they show up in the room
as their author said them and stay searchable as messages.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "db9718401a68"
down_revision: str | Sequence[str] | None = "e5c1a9f7b204"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("UPDATE blocks SET kind = 'message' WHERE kind = 'decision'")


def downgrade() -> None:
    """There is no way back: once converted, nothing tells a former decision from any
    other message."""
    pass
