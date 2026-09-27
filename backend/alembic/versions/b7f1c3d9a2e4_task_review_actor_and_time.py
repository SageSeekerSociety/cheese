"""task.reviewed_by / reviewed_at —— 谁在什么时候审的这道题

审题到今天为止不留痕：``PATCH /tasks/{id}`` 只写 ``approved`` 与
``reject_reason``，而 ``updated_at`` 每次 PATCH 都刷新（改标题、改截止也刷），
所以「最近处理过哪几道、谁处理的」没有任何一列答得上来。

这两列就是那件事本身：审核那一下写入审核人的 ``user.id`` 与当时的时间。驳回后
作者重新提交、再被审时，覆盖成最新一次。可空是给老数据留的 —— 这一列落地之前
审过的题谁也不知道是谁审的，NULL 就是「不知道」，不是「没审过」：审没审过仍然
只看 ``approved``。

Revision ID: b7f1c3d9a2e4
Revises: 7c2e91a4d3f6
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7f1c3d9a2e4"
down_revision: str | Sequence[str] | None = "7c2e91a4d3f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("task", sa.Column("reviewed_by", sa.Integer(), nullable=True))
    op.add_column(
        "task",
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("task", "reviewed_at")
    op.drop_column("task", "reviewed_by")
