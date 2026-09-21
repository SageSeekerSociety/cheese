"""a Space holds one shared compute pool

`compute_grants` could name a project (earmarked — a 赛题's resource pack) or a
team (every project in it), and nothing in between. A course that buys compute
for a class had to choose: one grant per student project, which nobody knows how
to size, or one team grant, which is the wrong scope for a 机构 that is not a
team. The 2026-09-14 decision is the middle one — the Space buys ONE pool, every
project whose 赛题 was published under that Space draws on it, first come first
served and capped in total.

The column is nullable and NULL on every existing row, so this migration changes
no reading: a row that named a project or a team keeps meaning exactly what it
meant. Nothing backfills, because there is nothing to infer one from — a grant
never recorded which Space its project belonged to, and guessing would hand
credits to whoever happens to sit in a Space today.

Deliberately NOT a foreign key, for the same reason `source_task_id` is not one
(see that column's comment): the credits were paid for, so the ledger has to
survive the Space being deleted. A `space` row that goes away takes its pools'
*reach* with it — `list_for_project` resolves the Space through the project's
赛题, and a project cannot reach a Space that no longer has that 赛题 — but the
audit trail of what was granted and spent stays readable.

One `ADD COLUMN` (metadata-only in Postgres 11+ — no table rewrite however large
the ledger) plus one index for the Space-level read, which is how a teacher's
"what is left in this pool" query finds the row.

Revision ID: a41dc6269f32
Revises: d3b8f1c72a94
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a41dc6269f32"
down_revision: str | Sequence[str] | None = "d3b8f1c72a94"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "compute_grants",
        sa.Column("space_id", sa.BigInteger(), nullable=True),
    )
    op.create_index("ix_compute_grants_space_id", "compute_grants", ["space_id"])


def downgrade() -> None:
    op.drop_index("ix_compute_grants_space_id", table_name="compute_grants")
    op.drop_column("compute_grants", "space_id")
