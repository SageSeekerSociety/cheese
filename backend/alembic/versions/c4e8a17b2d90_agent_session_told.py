"""What project state each agent conversation has been told

`agent_sessions.told` keeps a digest per section of the project state a
conversation has seen (topics, artifacts, roster, overview, memory index). The
system prompt no longer carries that state, so a resumed conversation is told
only the sections that changed since. NULL means nothing recorded yet, and the
next turn tells the whole state.

Revision ID: c4e8a17b2d90
Revises: cbf982a4af16
"""

import sqlalchemy as sa

from alembic import op

revision = "c4e8a17b2d90"
down_revision = "cbf982a4af16"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agent_sessions", sa.Column("told", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("agent_sessions", "told")
