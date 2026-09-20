"""blocks: partial index on the cloud-provisioning watermark

`BlockRepository.turn_history` runs at the top of every turn, and one of its
three arms asks for the room's newest 「机器正在创建并接入」 event. Nothing in
that predicate — `(meta ->> \'event_type\') = \'cloud_provisioning\'` — leads any
existing index, so the planner read the room\'s whole block line and filtered
it down, usually to nothing.

Measured on dev (2026-09-15, room 6be065fd with 1094 blocks on its line):

    before   Bitmap Heap Scan, Rows Removed by Filter: 1094, Heap Blocks: 625,
             Buffers: shared hit=1285; that arm alone 8.534 ms of the query\'s
             9.524 ms total
    after    see the receipt in the pull request

The cost grew with the room\'s history, so the oldest rooms — the ones someone
has actually been working in — paid the most, once per turn.

Partial rather than a fourth key column on the composite: these events are a
handful per room in a table that takes every message and every line of agent
output, so the index stays small and the insert path barely notices it. Plain
CREATE INDEX, as everywhere else here (b7e4d21c9a06 explains why CONCURRENTLY
is not an option inside alembic\'s transaction, and how the lock is bounded).
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c4d81f6a27b3"
down_revision: str | Sequence[str] | None = "ab798431c260"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_blocks_cloud_provisioning",
        "blocks",
        ["topic_id", "created_at", "id"],
        unique=False,
        postgresql_where=sa.text("(meta ->> 'event_type') = 'cloud_provisioning'"),
    )


def downgrade() -> None:
    op.drop_index("ix_blocks_cloud_provisioning", table_name="blocks")
