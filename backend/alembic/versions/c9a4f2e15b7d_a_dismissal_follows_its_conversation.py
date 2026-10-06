"""「不用」跟着它的对话走

Revision ID: c9a4f2e15b7d
Revises: 694b0dbaf5eb
Create Date: 2026-10-06

`feedback_proposal_dismissals.topic_id` 指向 `topics`（房间），而卡本身长在
`blocks` 上、按 `conversation_id` 挂 —— 任务也是对话，但任务没有 `topics` 行。于是
任务里点「不用」这一下，INSERT 被外键拒绝、路由回 500，而前端把那个错吞了：人看到
的是卡收起来、重进又回来。房间不受影响，所以它看着一直是好的，直到 fc3eab9b8847
把这类房间变成了任务。

改指向 `conversations`：同一段迁移给 `topic_read_states` 做的就是这件事，理由也一样
—— 一行属于的是**对话**，房间只是对话的一种，不是唯一的一种。
"""

from collections.abc import Sequence

from alembic import op

revision: str = "c9a4f2e15b7d"
down_revision: str | Sequence[str] | None = "694b0dbaf5eb"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CONSTRAINT = "feedback_proposal_dismissals_topic_id_fkey"


def upgrade() -> None:
    op.execute(f"ALTER TABLE feedback_proposal_dismissals DROP CONSTRAINT {CONSTRAINT}")
    op.execute(
        f"ALTER TABLE feedback_proposal_dismissals ADD CONSTRAINT {CONSTRAINT}"
        " FOREIGN KEY (topic_id) REFERENCES conversations (id) ON DELETE CASCADE"
    )


def downgrade() -> None:
    # 任务里记下的那些没有 `topics` 行可指，旧形状装不下：删掉它们，而不是让降级
    # 卡在外键上。
    op.execute(
        "DELETE FROM feedback_proposal_dismissals d"
        " WHERE NOT EXISTS (SELECT 1 FROM topics t WHERE t.id = d.topic_id)"
    )
    op.execute(f"ALTER TABLE feedback_proposal_dismissals DROP CONSTRAINT {CONSTRAINT}")
    op.execute(
        f"ALTER TABLE feedback_proposal_dismissals ADD CONSTRAINT {CONSTRAINT}"
        " FOREIGN KEY (topic_id) REFERENCES topics (id) ON DELETE CASCADE"
    )
