"""a session row has to name its harness

Revision ID: c9f41b7a2e08
Revises: a7f2c4d86b13
Create Date: 2026-09-21 23:30:00

``agent_sessions.harness`` has carried ``DEFAULT 'claude-code'`` since
ab798431c260, where it was a backfill value: the column arrived on a table full
of rows written before harness selection existed, and every one of them really
was Claude Code.

It has been the wrong thing to keep ever since. Which harness a turn runs on is
answered in one place — the deployment setting plus the project's override
(结论 28) — and the row is keyed by it, so an INSERT that says nothing does not
leave the field blank: it files the conversation under a harness it did not run
on, and the next turn hands that harness somebody else's resume token. The ORM
default came off in this same change; while the DDL one stays, the guarantee
holds only on a database built from the metadata, which is to say only in the
tests. Here it holds everywhere.

No data moves and nothing is rewritten — ``DROP DEFAULT`` touches the catalogue
only. The image serving requests during the window is safe under it because
every write path to this table goes through
``AgentSessionRepository._upsert``, which has passed ``harness`` explicitly for
as long as the column has existed.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c9f41b7a2e08"
down_revision: str | Sequence[str] | None = "a7f2c4d86b13"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "agent_sessions",
        "harness",
        existing_type=sa.String(64),
        existing_nullable=False,
        server_default=None,
    )


def downgrade() -> None:
    op.alter_column(
        "agent_sessions",
        "harness",
        existing_type=sa.String(64),
        existing_nullable=False,
        server_default="claude-code",
    )
