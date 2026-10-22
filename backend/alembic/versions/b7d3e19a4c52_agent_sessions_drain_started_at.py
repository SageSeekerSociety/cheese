"""agent_sessions: when the drain of a runner another release started began

Revision ID: b7d3e19a4c52
Revises: a3c7e1f9d240

A runner of another lifecycle protocol that is at work is drained before it is
replaced, for at most RUNNER_DRAIN_S. The cap counts from when the drain began,
so that is kept with the seat: a backend that takes the seat over in the middle
of a drain goes on counting from the same moment instead of starting again.
`drain_launch` names the runner that drain is of (its ping's launch), so a start
left behind by a drain that never settled does not count for another runner.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries

from alembic import op

revision: str = "b7d3e19a4c52"
down_revision: str | Sequence[str] | None = "a3c7e1f9d240"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with_lock_retries("agent_sessions")
    op.add_column(
        "agent_sessions",
        sa.Column("drain_started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "agent_sessions",
        sa.Column("drain_launch", sa.String(256), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("agent_sessions", "drain_launch")
    op.drop_column("agent_sessions", "drain_started_at")
