"""学情分析：每晚分析的结果、读到哪里、提问的归类、每天的调用次数

Revision ID: 4b7ef31b7b66
Revises: b3f7c1d94a20
Create Date: 2026-11-17

第二步（§ learning analysis, step 2）：每晚读成员在本空间题目项目里的发言，按知识点
给出三档判断。四张新表：

- ``space_learning_judgment``：成员 × 空间 × 知识点的**当前**判断（档位、一句话归纳、
  依据的 block id、是否被标「不准」）；
- ``space_learning_cursor``：成员 × 题目读到了哪条消息（增量游标）；
- ``space_learning_question``：每条提问归在哪个知识点、是哪一类（第 3 步按它聚合）；
- ``space_learning_run``：每块板每天调了几次模型（每日上限）以及当晚是否跑完。

新表不锁（旧发布不会读它们）；但这些表的外键指向 ``space``、``task`` 和
``space_knowledge_point``，三个都写进 ``with_lock_retries``
（``.claude/rules/migrations.md`` 规则 2）。新表上的索引不必 CONCURRENTLY（规则 5
对同一 PR 建的表豁免）。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "4b7ef31b7b66"
down_revision: str | Sequence[str] | None = "b3f7c1d94a20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with_lock_retries("space, task, space_knowledge_point")

    op.create_table(
        "space_learning_judgment",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("space_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("knowledge_point_id", sa.BigInteger(), nullable=False),
        sa.Column("level", sa.String(length=16), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column(
            "evidence_block_ids",
            postgresql.ARRAY(sa.UUID(as_uuid=True)),
            nullable=False,
        ),
        sa.Column("disputed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("analyzed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["space_id"],
            ["space.id"],
            name="fk_space_learning_judgment_space_id_space",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["knowledge_point_id"],
            ["space_knowledge_point.id"],
            # 目标表名省掉：带上会超过 Postgres 63 字的标识符上限（同
            # ``fk_task_knowledge_point_knowledge_point_id``）。
            name="fk_space_learning_judgment_knowledge_point_id",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "level IN ('mastered', 'shaky', 'weak')",
            name="ck_space_learning_judgment_level",
        ),
        # 每条判断都要指得出依据：空的依据清单写不进来。
        sa.CheckConstraint(
            "cardinality(evidence_block_ids) > 0",
            name="ck_space_learning_judgment_evidence",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_space_learning_judgment_space_user_point",
        "space_learning_judgment",
        ["space_id", "user_id", "knowledge_point_id"],
        unique=True,
    )
    op.create_index(
        "ix_space_learning_judgment_knowledge_point_id",
        "space_learning_judgment",
        ["knowledge_point_id"],
    )

    op.create_table(
        "space_learning_cursor",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("space_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("task_id", sa.BigInteger(), nullable=False),
        sa.Column("last_block_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_block_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "messages_read", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["space_id"],
            ["space.id"],
            name="fk_space_learning_cursor_space_id_space",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["task.id"],
            name="fk_space_learning_cursor_task_id_task",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_space_learning_cursor_space_user_task",
        "space_learning_cursor",
        ["space_id", "user_id", "task_id"],
        unique=True,
    )
    op.create_index(
        "ix_space_learning_cursor_task_id", "space_learning_cursor", ["task_id"]
    )

    op.create_table(
        "space_learning_question",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("space_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("task_id", sa.BigInteger(), nullable=False),
        sa.Column("block_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("knowledge_point_id", sa.BigInteger(), nullable=True),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("gist", sa.Text(), nullable=False),
        sa.Column("cluster_id", sa.BigInteger(), nullable=True),
        sa.Column("asked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["space_id"],
            ["space.id"],
            name="fk_space_learning_question_space_id_space",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["task.id"],
            name="fk_space_learning_question_task_id_task",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["knowledge_point_id"],
            ["space_knowledge_point.id"],
            name="fk_space_learning_question_knowledge_point_id",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            "kind IN ('concept', 'error', 'answer', 'verify', 'other')",
            name="ck_space_learning_question_kind",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_space_learning_question_block_id",
        "space_learning_question",
        ["block_id"],
        unique=True,
    )
    op.create_index(
        "ix_space_learning_question_space_user",
        "space_learning_question",
        ["space_id", "user_id"],
    )
    op.create_index(
        "ix_space_learning_question_task_id", "space_learning_question", ["task_id"]
    )
    op.create_index(
        "ix_space_learning_question_knowledge_point_id",
        "space_learning_question",
        ["knowledge_point_id"],
    )

    op.create_table(
        "space_learning_run",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("space_id", sa.BigInteger(), nullable=False),
        sa.Column("run_date", sa.Date(), nullable=False),
        sa.Column("calls", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "capped", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["space_id"],
            ["space.id"],
            name="fk_space_learning_run_space_id_space",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_space_learning_run_space_day",
        "space_learning_run",
        ["space_id", "run_date"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_space_learning_run_space_day", table_name="space_learning_run")
    op.drop_table("space_learning_run")
    op.drop_index(
        "ix_space_learning_question_knowledge_point_id",
        table_name="space_learning_question",
    )
    op.drop_index(
        "ix_space_learning_question_task_id", table_name="space_learning_question"
    )
    op.drop_index(
        "ix_space_learning_question_space_user", table_name="space_learning_question"
    )
    op.drop_index(
        "uq_space_learning_question_block_id", table_name="space_learning_question"
    )
    op.drop_table("space_learning_question")
    op.drop_index(
        "ix_space_learning_cursor_task_id", table_name="space_learning_cursor"
    )
    op.drop_index(
        "uq_space_learning_cursor_space_user_task", table_name="space_learning_cursor"
    )
    op.drop_table("space_learning_cursor")
    op.drop_index(
        "ix_space_learning_judgment_knowledge_point_id",
        table_name="space_learning_judgment",
    )
    op.drop_index(
        "uq_space_learning_judgment_space_user_point",
        table_name="space_learning_judgment",
    )
    op.drop_table("space_learning_judgment")
