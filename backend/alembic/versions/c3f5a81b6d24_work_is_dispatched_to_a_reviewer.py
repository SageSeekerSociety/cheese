"""work is dispatched to a reviewer

#718's settings page has a「任务默认 reviewer」and 派活时没指定就用这个 — but
nothing was reading it, so the setting existed and did nothing. This is the
column that makes it real: the reviewer is resolved when work is DISPATCHED
(explicit, else the project's default) and stored on the row, and 递卡 reuses it
unless the card names somebody else.

Stored rather than re-derived at 递卡 time on purpose: the project setting is a
policy that can change between dispatching a piece of work and delivering it,
while who that work was handed to is a fact about the moment it was handed over.

No backfill. Existing threads were dispatched before anyone could be named, and
writing today's project default onto them would claim they were handed to
someone who never saw them; a card for those threads names its reviewer the way
it always has.

Revision ID: c3f5a81b6d24
Revises: b1e4d2c90f77
Create Date: 2026-09-07
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c3f5a81b6d24"
down_revision: str | Sequence[str] | None = "b1e4d2c90f77"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("reviewer_handle", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column("tasks", "reviewer_handle")
