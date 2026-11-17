"""评测记录也能是一次不挂在题目上的试跑

方案 §出题人：配测试点 ——「用参考答案试跑」。出题人在出题表单里配好测试点、还没点发布
时就要能试跑，那时题目和测试套都还没有行，所以 ``task_judge_run`` 的 ``task_id``、
``suite_id`` 不能再是必填：试跑那一行这两格为空，跑什么写在 ``trial_spec`` 里，记在哪个
空间写进新加的 ``space_id``（算力记在这个空间的所有者头上）。

只放宽两列的 NOT NULL、加一个可空列：上一版后端照常读写这张表 —— 它只按提交读评测
记录，从不写试跑那种行，也就不会读到 ``task_id`` 为空的行。``space_id`` 不是外键，和
``project_id`` 一样：空间删了，这一次试跑照旧是记录。

辨认 ID: 342495d871e3
Revises: 9c1f4e7b2a35
Create Date: 2026-11-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries

from alembic import op

revision: str = "342495d871e3"
down_revision: str | Sequence[str] | None = "4007efa0f236"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with_lock_retries("task_judge_run")
    op.alter_column("task_judge_run", "task_id", nullable=True)
    op.alter_column("task_judge_run", "suite_id", nullable=True)
    op.add_column(
        "task_judge_run", sa.Column("space_id", sa.BigInteger(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("task_judge_run", "space_id")
    op.execute("DELETE FROM task_judge_run WHERE task_id IS NULL OR suite_id IS NULL")
    op.alter_column("task_judge_run", "suite_id", nullable=False)
    op.alter_column("task_judge_run", "task_id", nullable=False)
