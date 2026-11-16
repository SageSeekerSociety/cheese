"""学情分析：一块题目板的开关、知识点清单与告知确认记录

Revision ID: b3f7c1d94a20
Revises: 85dc942e2596
Create Date: 2026-11-16

第一步只有底座（§ learning analysis, step 1）：开关、知识点清单、题目↔知识点关联、
告知确认记录。分析、导出、巩固练习不在这一步里。

三张新表，外加 ``space`` 上的三列。新表不锁（旧发布永远不会读它们，
``168348b68d6c`` 同样处理）；但 ``space`` 和 ``task`` 都在这条迁移里被加了外键，
所以两个都要写进 ``with_lock_retries``（``.claude/rules/migrations.md`` 规则 2）。

``space.learning_analysis_enabled`` 带 ``server_default`` 而不是先可空后回填：
这是一个「关」的两值开关，旧行读成 false 正是它们的意思（``space`` 上没有一行
知道「学情分析」这个词），没有需要回填的第三种状态。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries

from alembic import op

revision: str = "b3f7c1d94a20"
down_revision: str | Sequence[str] | None = "85dc942e2596"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # space 与 task 都被加了外键（下面前两张表各指向 space，第三张指向 space 和
    # task），所以两个都列进来：漏掉的那个会在列出的表都拿住之后才去锁，
    # 而线上后端正等着它们，于是这次迁移与它死锁或超时。
    with_lock_retries("space, task")

    op.add_column(
        "space",
        sa.Column(
            "learning_analysis_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "space",
        sa.Column("learning_analysis_enabled_by", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "space",
        sa.Column(
            "learning_analysis_enabled_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )

    op.execute(sa.schema.CreateSequence(sa.Sequence("space_knowledge_point_seq")))

    op.create_table(
        "space_knowledge_point",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Sequence("space_knowledge_point_seq"),
            nullable=False,
        ),
        sa.Column("space_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("display_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["space_id"],
            ["space.id"],
            name="fk_space_knowledge_point_space_id_space",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "length(btrim(name)) > 0", name="ck_space_knowledge_point_name"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    # 一块板上一个知识点只活一行。部分索引（deleted_at IS NULL）：软删掉的那行
    # 是「移除过一次」的记录，之后再把这个名字加回来是一条新的生命，不是冲突。
    op.create_index(
        "uq_space_knowledge_point_live_name",
        "space_knowledge_point",
        ["space_id", "name"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    # 清单按 space_id 取，软删的行也要读得到（一块板列过的全部名字）。
    op.create_index(
        "ix_space_knowledge_point_space_id", "space_knowledge_point", ["space_id"]
    )

    op.create_table(
        "task_knowledge_point",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("task_id", sa.BigInteger(), nullable=False),
        sa.Column("knowledge_point_id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["task.id"],
            name="fk_task_knowledge_point_task_id_task",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["knowledge_point_id"],
            ["space_knowledge_point.id"],
            name="fk_task_knowledge_point_knowledge_point_id_space_knowledge_point",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    # 一道题在一个知识点下最多一行，交给索引而不是「先读后写」（两次点击会交错）。
    # 唯一索引而不是 UNIQUE 约束：模型那边也写成 ``Index(..., unique=True)``，
    # 两处形状一致 ``alembic check`` 才不会读出漂移（同 `a0f25891015d`）。
    op.create_index(
        "uq_task_knowledge_point_task_point",
        "task_knowledge_point",
        ["task_id", "knowledge_point_id"],
        unique=True,
    )
    # 「这个知识点下有哪些题」以及删掉知识点时按它级联，都要按 knowledge_point_id 走。
    op.create_index(
        "ix_task_knowledge_point_knowledge_point_id",
        "task_knowledge_point",
        ["knowledge_point_id"],
    )

    op.create_table(
        "space_learning_notice",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("space_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["space_id"],
            ["space.id"],
            name="fk_space_learning_notice_space_id_space",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    # 一个人在一块板上一条记录。唯一索引把并发两次点击也挡住，双击是空操作。
    # 也是 space_id 上的外键索引（首列）。
    op.create_index(
        "uq_space_learning_notice_space_user",
        "space_learning_notice",
        ["space_id", "user_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "uq_space_learning_notice_space_user", table_name="space_learning_notice"
    )
    op.drop_table("space_learning_notice")
    op.drop_index(
        "ix_task_knowledge_point_knowledge_point_id", table_name="task_knowledge_point"
    )
    op.drop_index(
        "uq_task_knowledge_point_task_point", table_name="task_knowledge_point"
    )
    op.drop_table("task_knowledge_point")
    op.drop_index("ix_space_knowledge_point_space_id", table_name="space_knowledge_point")
    op.drop_index(
        "uq_space_knowledge_point_live_name", table_name="space_knowledge_point"
    )
    op.drop_table("space_knowledge_point")
    op.execute(sa.schema.DropSequence(sa.Sequence("space_knowledge_point_seq")))
    op.drop_column("space", "learning_analysis_enabled_at")
    op.drop_column("space", "learning_analysis_enabled_by")
    op.drop_column("space", "learning_analysis_enabled")
