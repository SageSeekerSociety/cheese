"""blocks: index (topic_id, kind, task_id, created_at) INCLUDE (author)

话题级未读 (`TopicRepository.unread_counts`) counts, per topic of a project,
the message blocks on the room's own line that somebody else wrote after the
reader's cursor. Its only selective predicate — `project_id` — sits on
`topics`, not on `blocks`; everything it can say about `blocks` is
`kind = 'message' AND task_id IS NULL AND author <> me`, none of which any
existing index leads with. So the planner had one option: read the whole table.

Measured on a 10-project / 990,386-block / 625 MB database (the shape of a
platform that hosts many projects in one database, which is ours):

    before   Parallel Seq Scan on blocks, 440,250 rows filtered to yield 125
             numbers; Buffers: shared hit=22570 read=41096 (~500 MB);
             Execution Time: 234.022 ms
    after    Index Only Scan, Heap Fetches: 0; Buffers: shared hit=1295;
             Execution Time: 43.402 ms

That request is polled every 30 seconds by every open tab, and its cost grew
with the size of the WHOLE platform rather than with the project being looked
at — every additional tenant slowed down every existing tenant's sidebar.

`INCLUDE (author)` is what buys `Heap Fetches: 0`: `author <> me` is the last
predicate standing, and carrying it in the index leaf means the scan never
touches the heap at all. It is INCLUDE rather than a fifth key column because
nothing orders or ranges on author — it is only ever tested for inequality.

`created_at` is the fourth key column, not another INCLUDE: the read-cursor
comparison (`blocks.created_at > last_read_at`) ranges on it, and only a key
column can be used for that.

Replaces `ix_blocks_topic_id` rather than joining it. That index was a strict
prefix of `ix_blocks_topic_id_created_at` already — anything it could serve,
the composite could — and it is a strict prefix of this one too. Dropping it
keeps the index count on this table flat, so the write path (every message and
every line of agent output inserts here) pays nothing for the read win.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7e4d21c9a06"
down_revision: str | Sequence[str] | None = "d7a91c4e2b60"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Plain CREATE INDEX, not CONCURRENTLY, which is this repo's settled
    # practice (no migration uses CONCURRENTLY, and the closest precedent —
    # c1f7a3b90d24, an index on this same table — is a plain create). It is
    # also the only thing that works here: CONCURRENTLY cannot run inside a
    # transaction, and alembic wraps each migration in one. The lock is made
    # safe from the other end instead — `apply_migration_timeouts` sets
    # lock_timeout=10s so a migration that cannot get the lock fails fast and
    # the deploy is retryable, with statement_timeout=0 so the build itself is
    # never killed part-way.
    op.create_index(
        "ix_blocks_topic_kind_task_created",
        "blocks",
        ["topic_id", "kind", "task_id", "created_at"],
        unique=False,
        postgresql_include=["author"],
    )
    op.drop_index("ix_blocks_topic_id", table_name="blocks")


def downgrade() -> None:
    op.create_index("ix_blocks_topic_id", "blocks", ["topic_id"], unique=False)
    op.drop_index("ix_blocks_topic_kind_task_created", table_name="blocks")
