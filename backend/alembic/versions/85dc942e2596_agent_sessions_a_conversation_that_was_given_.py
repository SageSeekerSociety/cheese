"""agent_sessions: a conversation that was given up on

A conversation past its model's window whose own compaction failed cannot go
on: every later turn re-sends the same history and is refused the same way.
The platform marks it here and starts a new session on the next turn.

It is a column of its own rather than ``resume_token`` set to NULL: NULL is
already "this place has never run" (`agent_session.repositories.has_any`,
which answers the workspace's 现场 and the compute pin), and a room that ran
for hours does not stop having run because its conversation was given up on.

Revision ID: 85dc942e2596
Revises: a0f25891015d
Create Date: 2026-11-05 19:20:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "85dc942e2596"
down_revision: str | Sequence[str] | None = "a0f25891015d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_sessions",
        sa.Column("abandoned_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("agent_sessions", "abandoned_at")
