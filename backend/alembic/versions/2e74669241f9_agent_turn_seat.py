"""Record which agent's conversation a turn runs in

Revision ID: 2e74669241f9
Revises: 7b2d4e9c1a60

A room seats several teammates whose sessions run side by side, so the room a
turn ran in no longer says whose session it belongs to. The restart sweeps ask
exactly that: whether this turn's session still answers, and whether the agent
a waiting message named already has a turn running. Rows written before this
column have no agent, and are judged by their room as before.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "2e74669241f9"
down_revision: str | Sequence[str] | None = "7b2d4e9c1a60"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_turns", sa.Column("agent_handle", sa.String(length=64), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("agent_turns", "agent_handle")
