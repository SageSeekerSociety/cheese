"""评测记录可以是学生自测；判分程序的输出留一份学生看得到的

方案 §自测样例、§命令判分。两处：

* ``task_judge_run.purpose`` 多一种 ``SELF_TEST``：学生拿默认分支现在的代码只跑样例用例，
  不挂在哪一版提交上、不计分。CHECK 约束放宽成三种。
* 加可空列 ``student_output``：测试脚本和命令判分的输出里，出题人允许学生看的那前几行
  （评测那一刻的 ``student_log_lines``）；老师看的全文仍在 ``compile_output``。

都是放宽：上一版后端不写 ``SELF_TEST``、不读新列，照常读写这张表。降级时先删掉自测那几行，
再把约束收回去。

辨认 ID: b7d2e4f19a60
Revises: d81f3a6c2b95
Create Date: 2026-11-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries

from alembic import op

revision: str = "b7d2e4f19a60"
down_revision: str | Sequence[str] | None = "d81f3a6c2b95"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with_lock_retries("task_judge_run")
    op.drop_constraint("ck_task_judge_run_purpose", "task_judge_run", type_="check")
    # 加成 NOT VALID 不扫表；放宽了的条件旧行一定满足，随后在锁外 VALIDATE。
    op.create_check_constraint(
        "ck_task_judge_run_purpose",
        "task_judge_run",
        "purpose IN ('SUBMISSION', 'TRIAL', 'SELF_TEST')",
        postgresql_not_valid=True,
    )
    op.add_column(
        "task_judge_run", sa.Column("student_output", sa.Text(), nullable=True)
    )
    with op.get_context().autocommit_block():
        op.execute(
            "ALTER TABLE task_judge_run VALIDATE CONSTRAINT ck_task_judge_run_purpose"
        )


def downgrade() -> None:
    op.drop_column("task_judge_run", "student_output")
    op.execute("DELETE FROM task_judge_run WHERE purpose = 'SELF_TEST'")
    op.drop_constraint("ck_task_judge_run_purpose", "task_judge_run", type_="check")
    op.create_check_constraint(
        "ck_task_judge_run_purpose",
        "task_judge_run",
        "purpose IN ('SUBMISSION', 'TRIAL')",
        postgresql_not_valid=True,
    )
