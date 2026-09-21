"""drop the index that the bind call read

归属不再是「按分身 id 找那条绑过它的活」：子线程的事件自己带着卡的线程标识，
平台按标识直接读那张卡（结论 43）。`tasks.subagent_id` 这一列还在（卡上写「谁在
做、它还活着没有」，由平台在分身开工时写），但没有任何查询再按它找行，所以
(room_id, subagent_id) 这个索引只剩下每次写卡时要维护的成本。

Revision ID: a7f2c4d86b13
Revises: d3b8f1c72a94
"""

from collections.abc import Sequence

from alembic import op

revision: str = "a7f2c4d86b13"
down_revision: str | Sequence[str] | None = "d3b8f1c72a94"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("ix_tasks_room_id_subagent_id", table_name="tasks")


def downgrade() -> None:
    op.create_index("ix_tasks_room_id_subagent_id", "tasks", ["room_id", "subagent_id"])
