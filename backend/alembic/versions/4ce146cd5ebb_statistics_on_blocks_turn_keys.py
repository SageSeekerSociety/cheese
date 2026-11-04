"""blocks: statistics on the turn keys, so the planner prices their indexes

Revision ID: 4ce146cd5ebb
Revises: 321ac342821b
Create Date: 2026-11-01 01:10:00

`321ac342821b` indexed `(meta ->> 'prompted_turn')` and
`(meta ->> 'consumed_turn')`, partial on the key being present. PostgreSQL
keeps statistics on an index's expression only for an index without a WHERE,
so the planner priced `key = ANY(turns)` at its default selectivity, 0.5% of
the table per turn asked about. A room list asks about hundreds of failed
turns at once, the estimate passed the whole table, and dev kept reading every
block for `MemberWaits._turn_owners` (110-220 ms, 2026-11-01) with the
indexes in place.

Extended statistics on each expression (PostgreSQL 14+) give the planner the
real distribution: almost every block carries neither key, and each value is a
single turn. ANALYZE fills them now rather than whenever autovacuum next
samples `blocks`; it takes SHARE UPDATE EXCLUSIVE, which reads and writes do
not wait on.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "4ce146cd5ebb"
down_revision: str | Sequence[str] | None = "321ac342821b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STATISTICS = (
    ("st_blocks_prompted_turn", "prompted_turn"),
    ("st_blocks_consumed_turn", "consumed_turn"),
)


def upgrade() -> None:
    with op.get_context().autocommit_block():
        for name, key in STATISTICS:
            op.execute(
                f"CREATE STATISTICS IF NOT EXISTS {name} "
                f"ON ((meta ->> '{key}')) FROM blocks"
            )
        op.execute("ANALYZE blocks")


def downgrade() -> None:
    with op.get_context().autocommit_block():
        for name, _ in STATISTICS:
            op.execute(f"DROP STATISTICS IF EXISTS {name}")
