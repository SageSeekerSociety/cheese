"""blocks: index (topic_id, created_at) for a topic's 最后活动时间

A topic's last activity is MAX(blocks.created_at) evaluated once per listed
topic (话题列表按最后活动排序/过滤). With only the single-column topic_id index
that means reading every block of every topic in the project — a busy topic
holds thousands of event blocks. The composite index turns each one into a
backwards index scan, and the timeline queries (WHERE topic_id = ? ORDER BY
created_at) get it too. Index only; no data change.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c1f7a3b90d24"
# Re-chained twice post-merge: three concurrent PRs (#298, #289, #301, #304)
# each passed CI green on an older base, and main ended up with THREE heads —
# `upgrade head` refused, deploys stopped. Linearized in merge order; this
# index migration is order-independent, so it goes last.
down_revision: str | Sequence[str] | None = "a1c9e7b30d42"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_blocks_topic_id_created_at",
        "blocks",
        ["topic_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_blocks_topic_id_created_at", table_name="blocks")
