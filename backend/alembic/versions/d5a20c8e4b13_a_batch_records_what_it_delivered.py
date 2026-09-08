"""a batch records what it delivered

The commit a batch actually merged, written at merge time and never touched
again. A device that keeps working past a delivery has to carry its next batch
onto the current base, and the base of that three-way merge must be **what was
delivered** — not the delivered branch's tip, which is mutable: anything pushed
onto that branch afterwards (a stale screen, a hand push) would read as
delivered content while never having been near main, and the merge would then
silently re-deliver or drop work.

No backfill: for a batch that merged before this column existed there is no
honest value to write, and inventing one would be inventing the very fact the
column exists to be trusted for. A device asking about such a batch is refused
and says so, which is the correct answer.

Revision ID: d5a20c8e4b13
Revises: c3f5a81b6d24
Create Date: 2026-09-08
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d5a20c8e4b13"
down_revision: str | Sequence[str] | None = "c3f5a81b6d24"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("work_trees", sa.Column("delivered_head", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column("work_trees", "delivered_head")
