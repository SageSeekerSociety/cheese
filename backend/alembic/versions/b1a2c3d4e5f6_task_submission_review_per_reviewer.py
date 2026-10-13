"""批阅按人分开：`task_submission_review` 加 `reviewer_id`，唯一性改成 (submission_id, reviewer_id)

一版提交可以有多位老师各判一份。这一列记「谁判的」（`user.id`），唯一性从隐式的
「一版一条」（此前只在服务层挡）改成显式的 `(submission_id, reviewer_id)`：同一个人
在同一版上仍只有一条，不同的人各判各的。

回填：历史行不知道是谁判的。这里把每一行回填成 `-id`（负数，永远不是真实用户 id），
每一行各不相同，既保住唯一，又如实标出「历史行、判者不明」。唯一索引带
`deleted_at IS NULL`（只看活行）：软删一条再重判是允许的，同一个人在**同一版上活着
的**评审仍只有一条。升级方向不丢数据。

Revision ID: b1a2c3d4e5f6
Revises: f3b8d2c7a1e5
Create Date: 2026-10-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries

from alembic import op

revision: str = "b1a2c3d4e5f6"
down_revision: str | Sequence[str] | None = "f3b8d2c7a1e5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CONSTRAINT = "uq_task_submission_review_submission_reviewer"


def upgrade() -> None:
    with_lock_retries("task_submission_review")
    op.add_column(
        "task_submission_review",
        sa.Column("reviewer_id", sa.Integer(), nullable=True),
    )
    # 每行一个各不相同的负数 —— 见模块 docstring。
    op.execute(
        "UPDATE task_submission_review SET reviewer_id = -id WHERE reviewer_id IS NULL"
    )
    op.alter_column("task_submission_review", "reviewer_id", nullable=False)
    # 部分唯一索引：只看活行。软删一条再重判是允许的（同一个人同一版上活着的
    # 评审仍只有一条），所以带上 `deleted_at IS NULL`。
    op.create_index(
        _CONSTRAINT,
        "task_submission_review",
        ["submission_id", "reviewer_id"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    with_lock_retries("task_submission_review")
    op.drop_index(_CONSTRAINT, table_name="task_submission_review")
    op.drop_column("task_submission_review", "reviewer_id")
