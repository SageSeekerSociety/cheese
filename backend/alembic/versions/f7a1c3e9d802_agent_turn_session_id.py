"""the conversation a turn ran in (FB-56 legacy③)

Revision ID: f7a1c3e9d802
Revises: e6f9b2d5c801
Create Date: 2026-10-01 10:00:00.000000

A termination's evidence names a conversation, so a row can only be matched
to it if the row says which conversation it ran in. Stamped when the session
itself reports its id (the AgentSessionInfo the harness sends), never
inferred from pointers or positions. NULL for every row written before this
column existed — and those rows are not attributable to any conversation's
death, by construction.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f7a1c3e9d802"
down_revision: str | Sequence[str] | None = "e6f9b2d5c801"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_turns",
        sa.Column("session_id", sa.String(64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("agent_turns", "session_id")
