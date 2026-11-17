"""测试点与评测结果的表，提交记下评测的是哪一版代码

方案 §11「数据结构」。#4767 的第 1 步（① 打分底座）：在数据库里留出放测试点、放评测
结果的地方，并记下每一版提交是学生项目里的哪一版代码。评分要点那一列的「评分方式」
不在这里加 —— 主干已有的 ``task_rubric_item.kind`` 就是干这个的（``e5a17c3d9b42``），
这一步只给它多一种取值 ``tests``（由那一阶段的评测结果折算），没有约束要改
（``kind`` 上没有 CHECK）。

三张新表都在这一步建全（测试套、测试用例、评测记录），三种测试形态的列都有：
第 1 步只有「输入/输出用例」这一种形态出得来，但第 2 步要的脚本形态和命令判分形态
的列（``commands``、``score_source``、``protected_paths``、``test_files``、
``install_deps``、``total_time_limit_s``、``student_log_lines``、``self_test_daily``、
``task_test_case.test_id``）现在就留好，免得再动一次这几张热表。三张表都是新的，上一版
后端不读它们，写进去的行也就不会有人看见。

改的那张旧表只加一列，是常量默认值，所以加列不重写表，上一版后端照常读写：它不认识
``code_sha``，插的行取默认值（空串 —— 正是它在上一版行为下的含义）。

辨认 ID: 9c1f4e7b2a35
Revises: e5a17c3d9b42
Create Date: 2026-11-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "9c1f4e7b2a35"
down_revision: str | Sequence[str] | None = "b3f7c1d94a20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = "task_submission, task"


def upgrade() -> None:
    # 新表的两个外键指向 task 与 task_submission，所以这两张也要点名。
    with_lock_retries(_TABLES)
    # 交这一版时学生项目默认分支的代码版本；以前的行是空串（没记下）。
    op.add_column(
        "task_submission",
        sa.Column("code_sha", sa.String(64), nullable=False, server_default=""),
    )

    op.execute(sa.schema.CreateSequence(sa.Sequence("task_test_suite_seq")))
    op.execute(sa.schema.CreateSequence(sa.Sequence("task_test_case_seq")))
    op.execute(sa.schema.CreateSequence(sa.Sequence("task_judge_run_seq")))

    op.create_table(
        "task_test_suite",
        sa.Column(
            "id", sa.BigInteger(), sa.Sequence("task_test_suite_seq"), nullable=False
        ),
        sa.Column("task_id", sa.BigInteger(), nullable=False),
        sa.Column("stage", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("kind", sa.String(16), nullable=False, server_default="IO"),
        sa.Column(
            "runtime", sa.String(32), nullable=False, server_default="python3.12"
        ),
        sa.Column("code_dir", sa.String(255), nullable=False, server_default=""),
        sa.Column("compile_cmd", sa.Text(), nullable=False, server_default=""),
        sa.Column("run_cmd", sa.Text(), nullable=False, server_default=""),
        sa.Column("time_limit_ms", sa.Integer(), nullable=False, server_default="1000"),
        sa.Column("memory_mb", sa.Integer(), nullable=False, server_default="256"),
        sa.Column(
            "compare", sa.String(16), nullable=False, server_default="TRIM_TRAILING"
        ),
        sa.Column("tolerance", sa.Float(), nullable=False, server_default="0"),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "show_hidden_names",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("commands", postgresql.JSONB(), nullable=True),
        sa.Column("score_source", sa.String(16), nullable=True),
        sa.Column("protected_paths", postgresql.JSONB(), nullable=True),
        sa.Column("test_files", postgresql.JSONB(), nullable=True),
        sa.Column(
            "install_deps",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("total_time_limit_s", sa.Integer(), nullable=True),
        sa.Column(
            "student_log_lines", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("self_test_daily", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "kind IN ('IO', 'SCRIPT', 'COMMAND')", name="ck_task_test_suite_kind"
        ),
        sa.CheckConstraint(
            "compare IN ('TRIM_TRAILING', 'EXACT', 'NUMERIC', 'IGNORE_CASE')",
            name="ck_task_test_suite_compare",
        ),
        sa.CheckConstraint(
            "score_source IS NULL OR score_source IN ('LINE', 'JSON')",
            name="ck_task_test_suite_score_source",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["task.id"],
            name="fk_task_test_suite_task_id_task",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "task_id", "stage", name="uq_task_test_suite_task_id_stage"
        ),
    )
    op.create_index("ix_task_test_suite_task_id", "task_test_suite", ["task_id"])

    op.create_table(
        "task_test_case",
        sa.Column(
            "id", sa.BigInteger(), sa.Sequence("task_test_case_seq"), nullable=False
        ),
        sa.Column("suite_id", sa.BigInteger(), nullable=False),
        sa.Column("index", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("points", sa.Integer(), nullable=False),
        sa.Column("visibility", sa.String(16), nullable=False, server_default="SAMPLE"),
        sa.Column("test_id", sa.String(128), nullable=True),
        sa.Column("input_location", sa.String(16), nullable=True),
        sa.Column("input_key", sa.String(1024), nullable=True),
        sa.Column("input_bytes", sa.BigInteger(), nullable=True),
        sa.Column("expected_location", sa.String(16), nullable=True),
        sa.Column("expected_key", sa.String(1024), nullable=True),
        sa.Column("expected_bytes", sa.BigInteger(), nullable=True),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "visibility IN ('SAMPLE', 'HIDDEN')", name="ck_task_test_case_visibility"
        ),
        sa.ForeignKeyConstraint(
            ["suite_id"],
            ["task_test_suite.id"],
            name="fk_task_test_case_suite_id_task_test_suite",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_task_test_case_suite_id", "task_test_case", ["suite_id"])

    op.create_table(
        "task_judge_run",
        sa.Column(
            "id", sa.BigInteger(), sa.Sequence("task_judge_run_seq"), nullable=False
        ),
        sa.Column("task_id", sa.BigInteger(), nullable=False),
        sa.Column("suite_id", sa.BigInteger(), nullable=False),
        sa.Column("suite_revision", sa.Integer(), nullable=False),
        sa.Column("submission_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "purpose", sa.String(16), nullable=False, server_default="SUBMISSION"
        ),
        sa.Column("trial_spec", postgresql.JSONB(), nullable=True),
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column("code_sha", sa.String(64), nullable=False, server_default=""),
        sa.Column("requested_by", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("state", sa.String(16), nullable=False, server_default="queued"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("claim_token", sa.Uuid(), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("device_id", sa.String(64), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("machine_ms", sa.Integer(), nullable=True),
        sa.Column("compile_output", sa.Text(), nullable=True),
        sa.Column("results", postgresql.JSONB(), nullable=True),
        sa.Column("passed_points", sa.Integer(), nullable=True),
        sa.Column("total_points", sa.Integer(), nullable=True),
        sa.Column("error", postgresql.JSONB(), nullable=True),
        sa.Column("credits", sa.Float(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "purpose IN ('SUBMISSION', 'TRIAL')", name="ck_task_judge_run_purpose"
        ),
        sa.CheckConstraint(
            "state IN ('queued', 'running', 'done', 'failed')",
            name="ck_task_judge_run_state",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["task.id"],
            name="fk_task_judge_run_task_id_task",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["suite_id"],
            ["task_test_suite.id"],
            name="fk_task_judge_run_suite_id_task_test_suite",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["submission_id"],
            ["task_submission.id"],
            name="fk_task_judge_run_submission_id_task_submission",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_task_judge_run_task_id", "task_judge_run", ["task_id"])
    op.create_index("ix_task_judge_run_suite_id", "task_judge_run", ["suite_id"])
    op.create_index(
        "ix_task_judge_run_submission_id", "task_judge_run", ["submission_id"]
    )
    # 领任务就是「拿最早那个排队的」。
    op.create_index(
        "ix_task_judge_run_queued",
        "task_judge_run",
        ["state", "created_at"],
        postgresql_where=sa.text("state = 'queued'"),
    )


def downgrade() -> None:
    with_lock_retries(_TABLES)
    op.drop_table("task_judge_run")
    op.drop_table("task_test_case")
    op.drop_table("task_test_suite")
    op.drop_column("task_submission", "code_sha")
    op.execute(sa.schema.DropSequence(sa.Sequence("task_judge_run_seq")))
    op.execute(sa.schema.DropSequence(sa.Sequence("task_test_case_seq")))
    op.execute(sa.schema.DropSequence(sa.Sequence("task_test_suite_seq")))
