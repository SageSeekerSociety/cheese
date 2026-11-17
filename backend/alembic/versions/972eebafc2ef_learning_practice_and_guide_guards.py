"""学情分析：巩固练习、复习排期、推送、讲解缓存、每人每日调用，与引导模式的记录

Revision ID: 972eebafc2ef
Revises: 4007efa0f236
Create Date: 2026-11-17

第四步（§ learning analysis, step 4）。六张新表：

- ``space_learning_push``：老师按下的一次「推送巩固题」，只记人数；
- ``space_learning_practice``：一组巩固题与这个人的作答（一个人在一个知识点上至多一组
  没做完的，部分唯一索引）；
- ``space_learning_review``：一人一个知识点的复习排期；
- ``space_learning_explanation``：「先看一段讲解」的缓存；
- ``space_learning_member_day``：一个人一天替自己要了几次模型调用；
- ``task_guide_event``：引导模式的提示阶梯 / 输出检查对一条 AI 回复做了什么。

``space_learning_judgment`` 加三列可空的作答统计（规则 3：可空，不回填）。全是新表，
索引不必 ``CONCURRENTLY``（规则 5 的豁免）。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "972eebafc2ef"
down_revision: str | Sequence[str] | None = "4007efa0f236"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _space_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["space_id"],
        ["space.id"],
        name=f"fk_{table}_space_id_space",
        ondelete="CASCADE",
    )


def _point_fk(table: str) -> sa.ForeignKeyConstraint:
    # 目标表名省掉：带上会超过 Postgres 63 字的标识符上限（同第 2、3 步）。
    return sa.ForeignKeyConstraint(
        ["knowledge_point_id"],
        ["space_knowledge_point.id"],
        name=f"fk_{table}_knowledge_point_id",
        ondelete="CASCADE",
    )


def _stamp(name: str, *, nullable: bool = False) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def upgrade() -> None:
    with_lock_retries(
        "space, space_knowledge_point, space_learning_judgment, task, projects"
    )

    op.add_column(
        "space_learning_judgment",
        sa.Column("practice_correct", sa.Integer(), nullable=True),
    )
    op.add_column(
        "space_learning_judgment",
        sa.Column("practice_total", sa.Integer(), nullable=True),
    )
    op.add_column(
        "space_learning_judgment",
        sa.Column("practiced_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "space_learning_push",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("space_id", sa.BigInteger(), nullable=False),
        sa.Column("knowledge_point_id", sa.BigInteger(), nullable=False),
        sa.Column("pushed_by", sa.BigInteger(), nullable=False),
        sa.Column("recipients", sa.Integer(), nullable=False),
        _stamp("created_at"),
        _space_fk("space_learning_push"),
        _point_fk("space_learning_push"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_space_learning_push_space_id", "space_learning_push", ["space_id"]
    )
    op.create_index(
        "ix_space_learning_push_knowledge_point_id",
        "space_learning_push",
        ["knowledge_point_id"],
    )

    op.create_table(
        "space_learning_practice",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("space_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("knowledge_point_id", sa.BigInteger(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("push_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "questions",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("answers", postgresql.ARRAY(sa.Integer()), nullable=True),
        sa.Column("correct", sa.Integer(), nullable=True),
        sa.Column("level_before", sa.String(length=16), nullable=True),
        sa.Column("level_after", sa.String(length=16), nullable=True),
        _stamp("created_at"),
        _stamp("generated_at", nullable=True),
        _stamp("completed_at", nullable=True),
        _space_fk("space_learning_practice"),
        _point_fk("space_learning_practice"),
        sa.ForeignKeyConstraint(
            ["push_id"],
            ["space_learning_push.id"],
            name="fk_space_learning_practice_push_id",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            "source IN ('self', 'pushed', 'review')",
            name="ck_space_learning_practice_source",
        ),
        sa.CheckConstraint(
            "level_before IS NULL OR level_before IN ('mastered', 'shaky', 'weak')",
            name="ck_space_learning_practice_level_before",
        ),
        sa.CheckConstraint(
            "level_after IS NULL OR level_after IN ('mastered', 'shaky', 'weak')",
            name="ck_space_learning_practice_level_after",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_space_learning_practice_space_user",
        "space_learning_practice",
        ["space_id", "user_id", "knowledge_point_id"],
    )
    op.create_index(
        "uq_space_learning_practice_open",
        "space_learning_practice",
        ["space_id", "user_id", "knowledge_point_id"],
        unique=True,
        postgresql_where=sa.text("completed_at IS NULL"),
    )
    op.create_index(
        "ix_space_learning_practice_knowledge_point_id",
        "space_learning_practice",
        ["knowledge_point_id"],
    )
    op.create_index(
        "ix_space_learning_practice_push_id", "space_learning_practice", ["push_id"]
    )

    op.create_table(
        "space_learning_review",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("space_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("knowledge_point_id", sa.BigInteger(), nullable=False),
        sa.Column("streak", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("due_on", sa.Date(), nullable=True),
        _stamp("last_practiced_at"),
        _stamp("created_at"),
        _stamp("updated_at"),
        _space_fk("space_learning_review"),
        _point_fk("space_learning_review"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_space_learning_review_space_user_point",
        "space_learning_review",
        ["space_id", "user_id", "knowledge_point_id"],
        unique=True,
    )
    op.create_index(
        "ix_space_learning_review_knowledge_point_id",
        "space_learning_review",
        ["knowledge_point_id"],
    )

    op.create_table(
        "space_learning_explanation",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("space_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("knowledge_point_id", sa.BigInteger(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        _stamp("basis_at"),
        _stamp("generated_at"),
        _stamp("created_at"),
        _stamp("updated_at"),
        _space_fk("space_learning_explanation"),
        _point_fk("space_learning_explanation"),
        sa.CheckConstraint(
            "length(btrim(body)) > 0", name="ck_space_learning_explanation_body"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_space_learning_explanation_space_user_point",
        "space_learning_explanation",
        ["space_id", "user_id", "knowledge_point_id"],
        unique=True,
    )
    op.create_index(
        "ix_space_learning_explanation_knowledge_point_id",
        "space_learning_explanation",
        ["knowledge_point_id"],
    )

    op.create_table(
        "space_learning_member_day",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("space_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("calls", sa.Integer(), server_default=sa.text("0"), nullable=False),
        _stamp("created_at"),
        _stamp("updated_at"),
        _space_fk("space_learning_member_day"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_space_learning_member_day_space_user_day",
        "space_learning_member_day",
        ["space_id", "user_id", "day"],
        unique=True,
    )

    op.create_table(
        "task_guide_event",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("task_id", sa.BigInteger(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=True),
        sa.Column("block_id", sa.UUID(), nullable=True),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("level", sa.Integer(), nullable=True),
        _stamp("created_at"),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["task.id"],
            name="fk_task_guide_event_task_id_task",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_task_guide_event_project_id_projects",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "kind IN ('hint', 'output_check')", name="ck_task_guide_event_kind"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_task_guide_event_task_id", "task_guide_event", ["task_id"])
    op.create_index(
        "ix_task_guide_event_project_id", "task_guide_event", ["project_id"]
    )


def downgrade() -> None:
    op.drop_table("task_guide_event")
    op.drop_table("space_learning_member_day")
    op.drop_table("space_learning_explanation")
    op.drop_table("space_learning_review")
    op.drop_table("space_learning_practice")
    op.drop_table("space_learning_push")
    op.drop_column("space_learning_judgment", "practiced_at")
    op.drop_column("space_learning_judgment", "practice_total")
    op.drop_column("space_learning_judgment", "practice_correct")
