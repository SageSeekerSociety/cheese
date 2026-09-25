"""agent_turns.route / reply_to —— what ending a turn needs, kept on the turn

A rollout hands running turns from one backend process to the next (#1723). The
next process finds each turn again and closes it when the session stops, but two
things it needs for that lived only in the memory of the process that started
it: where the turn's model traffic went, which decides how its spend is read
back, and the message it answers. They are written on the turn's row when it is
assembled.

Both nullable: a turn a session started by itself was never assembled, and rows
written before this revision carry neither. The previous revision ignores both
columns, so downgrading loses only what they record.

Revision ID: d4e7a2c91b35
Revises: 853d38c772dd
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d4e7a2c91b35"
down_revision: str | Sequence[str] | None = "853d38c772dd"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("agent_turns", sa.Column("route", sa.String(32), nullable=True))
    op.add_column("agent_turns", sa.Column("reply_to", sa.Uuid(), nullable=True))


def downgrade() -> None:
    op.drop_column("agent_turns", "reply_to")
    op.drop_column("agent_turns", "route")
