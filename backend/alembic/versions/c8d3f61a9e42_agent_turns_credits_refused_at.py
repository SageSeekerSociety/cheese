"""agent_turns.credits_refused_at —— admission's own stamp, not Claude Code's reading

#715: a spent budget made the metering proxy 429 every `/v1/messages` call, and
after ten retries Claude Code read that as `authentication_failed`, so the room
saw "Invalid API key" — wrong advice for a spent balance. The admission endpoint
is the one place that actually knows why it refused; this column is where it
records that against the turn it refused, so the turn's own end can repeat the
platform's wording instead of Claude Code's.

First-writer-wins, like `delivered_at`: the proxy caches a verdict for 30s and
Claude Code retries ten times, so admission is asked again and again for the
same refusal. Whichever call flips this column from NULL is the one that posts
the room notice.

Revision ID: c8d3f61a9e42
Revises: b3f1c7d9a204
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c8d3f61a9e42"
down_revision: str | Sequence[str] | None = "b3f1c7d9a204"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_turns",
        sa.Column("credits_refused_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("agent_turns", "credits_refused_at")
