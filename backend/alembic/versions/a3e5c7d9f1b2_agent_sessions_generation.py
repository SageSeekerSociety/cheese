"""agent_sessions.generation: which rulebook of its kind a session started under

Revision ID: a3e5c7d9f1b2
Revises: 4b7ef31b7b66
Create Date: 2026-11-17

A session started under one generation of its kind's rules is not resumed
under another (`prompts.Profile.generation`). Rows that exist now were started
before generations were counted: they take 0, which is older than the channel
main line's current generation and the same as every other kind's, so each
existing main line starts one new session and nothing else restarts.

NOT NULL with a constant default: on PostgreSQL 11+ that is a catalog change,
and the previous release, which does not map the column, inserts rows that
take the default.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries

from alembic import op

revision: str = "a3e5c7d9f1b2"
down_revision: str | Sequence[str] | None = "4b7ef31b7b66"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with_lock_retries("agent_sessions")
    op.add_column(
        "agent_sessions",
        sa.Column("generation", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("agent_sessions", "generation")
