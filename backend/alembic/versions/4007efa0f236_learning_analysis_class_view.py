"""学情分析：高频问题簇、常见误解、讲课建议、按周快照、导出记录

Revision ID: 4007efa0f236
Revises: 4b7ef31b7b66
Create Date: 2026-11-17

第三步（§ learning analysis, step 3）：老师端「学情分析」七个页签要的几样东西。五张
新表：

- ``space_learning_cluster``：意思相近的提问归成的一簇（每晚分析的聚类那一步写）；
- ``space_learning_misconception``：一个知识点下的一种常见误解（随判断一起归纳）；
- ``space_learning_advice``：一个知识点的讲课建议（每晚为卡住最多的几个刷新）；
- ``space_learning_week``：一周里一个知识点卡住多少人（判断只存当前，趋势靠它）；
- ``space_learning_export``：谁在什么时候导出了一份学情报告。

两张第 2 步的表各接上一列外键：``space_learning_judgment.misconception_id``（新列，
可空）与 ``space_learning_question.cluster_id``（第 2 步留好的列，一直是 NULL）。外键
按规则 6 先 ``NOT VALID`` 加上、再单独 ``VALIDATE``；两列的索引按规则 5
``CONCURRENTLY`` 建（表是已有的）。``VALIDATE`` 与建索引都放在 autocommit 块里，
两者都可重跑（``VALIDATE`` 再来一次什么也不做，索引带 ``IF NOT EXISTS``）。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "4007efa0f236"
down_revision: str | Sequence[str] | None = "4b7ef31b7b66"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _space_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["space_id"],
        ["space.id"],
        name=f"fk_{table}_space_id_space",
        ondelete="CASCADE",
    )


def _point_fk(table: str, ondelete: str) -> sa.ForeignKeyConstraint:
    # 目标表名省掉：带上会超过 Postgres 63 字的标识符上限（同第 2 步）。
    return sa.ForeignKeyConstraint(
        ["knowledge_point_id"],
        ["space_knowledge_point.id"],
        name=f"fk_{table}_knowledge_point_id",
        ondelete=ondelete,
    )


def upgrade() -> None:
    with_lock_retries(
        "space, space_knowledge_point, space_learning_judgment, space_learning_question"
    )

    op.create_table(
        "space_learning_cluster",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("space_id", sa.BigInteger(), nullable=False),
        sa.Column("knowledge_point_id", sa.BigInteger(), nullable=True),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        _space_fk("space_learning_cluster"),
        _point_fk("space_learning_cluster", "SET NULL"),
        sa.CheckConstraint(
            "length(btrim(label)) > 0", name="ck_space_learning_cluster_label"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_space_learning_cluster_space_id", "space_learning_cluster", ["space_id"]
    )
    op.create_index(
        "ix_space_learning_cluster_knowledge_point_id",
        "space_learning_cluster",
        ["knowledge_point_id"],
    )

    op.create_table(
        "space_learning_misconception",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("space_id", sa.BigInteger(), nullable=False),
        sa.Column("knowledge_point_id", sa.BigInteger(), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        _space_fk("space_learning_misconception"),
        _point_fk("space_learning_misconception", "CASCADE"),
        sa.CheckConstraint(
            "length(btrim(label)) > 0", name="ck_space_learning_misconception_label"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_space_learning_misconception_point_label",
        "space_learning_misconception",
        ["knowledge_point_id", "label"],
        unique=True,
    )
    op.create_index(
        "ix_space_learning_misconception_space_id",
        "space_learning_misconception",
        ["space_id"],
    )

    op.create_table(
        "space_learning_advice",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("space_id", sa.BigInteger(), nullable=False),
        sa.Column("knowledge_point_id", sa.BigInteger(), nullable=False),
        sa.Column("advice", sa.Text(), nullable=False),
        sa.Column("stuck_count", sa.Integer(), nullable=False),
        sa.Column(
            "misconception_ids", postgresql.ARRAY(sa.BigInteger()), nullable=False
        ),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        _space_fk("space_learning_advice"),
        _point_fk("space_learning_advice", "CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_space_learning_advice_knowledge_point_id",
        "space_learning_advice",
        ["knowledge_point_id"],
        unique=True,
    )
    op.create_index(
        "ix_space_learning_advice_space_id", "space_learning_advice", ["space_id"]
    )

    op.create_table(
        "space_learning_week",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("space_id", sa.BigInteger(), nullable=False),
        sa.Column("knowledge_point_id", sa.BigInteger(), nullable=False),
        sa.Column("week_start", sa.Date(), nullable=False),
        sa.Column("stuck_count", sa.Integer(), nullable=False),
        sa.Column("involved_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        _space_fk("space_learning_week"),
        _point_fk("space_learning_week", "CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_space_learning_week_space_point_week",
        "space_learning_week",
        ["space_id", "knowledge_point_id", "week_start"],
        unique=True,
    )
    op.create_index(
        "ix_space_learning_week_knowledge_point_id",
        "space_learning_week",
        ["knowledge_point_id"],
    )

    op.create_table(
        "space_learning_export",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("space_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("format", sa.String(length=8), nullable=False),
        sa.Column("sections", postgresql.ARRAY(sa.String(length=32)), nullable=False),
        sa.Column("knowledge_point_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        _space_fk("space_learning_export"),
        _point_fk("space_learning_export", "SET NULL"),
        sa.CheckConstraint(
            "format IN ('pdf', 'xlsx', 'docx')", name="ck_space_learning_export_format"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_space_learning_export_space_id", "space_learning_export", ["space_id"]
    )
    op.create_index(
        "ix_space_learning_export_knowledge_point_id",
        "space_learning_export",
        ["knowledge_point_id"],
    )

    op.add_column(
        "space_learning_judgment",
        sa.Column("misconception_id", sa.BigInteger(), nullable=True),
    )
    op.create_foreign_key(
        "fk_space_learning_judgment_misconception_id",
        "space_learning_judgment",
        "space_learning_misconception",
        ["misconception_id"],
        ["id"],
        ondelete="SET NULL",
        postgresql_not_valid=True,
    )
    op.create_foreign_key(
        "fk_space_learning_question_cluster_id",
        "space_learning_question",
        "space_learning_cluster",
        ["cluster_id"],
        ["id"],
        ondelete="SET NULL",
        postgresql_not_valid=True,
    )

    with op.get_context().autocommit_block():
        op.execute(
            "ALTER TABLE space_learning_judgment "
            "VALIDATE CONSTRAINT fk_space_learning_judgment_misconception_id"
        )
        op.execute(
            "ALTER TABLE space_learning_question "
            "VALIDATE CONSTRAINT fk_space_learning_question_cluster_id"
        )
        op.create_index(
            "ix_space_learning_judgment_misconception_id",
            "space_learning_judgment",
            ["misconception_id"],
            postgresql_concurrently=True,
            if_not_exists=True,
        )
        op.create_index(
            "ix_space_learning_question_cluster_id",
            "space_learning_question",
            ["cluster_id"],
            postgresql_concurrently=True,
            if_not_exists=True,
        )


def downgrade() -> None:
    op.drop_index(
        "ix_space_learning_question_cluster_id", table_name="space_learning_question"
    )
    op.drop_constraint(
        "fk_space_learning_question_cluster_id",
        "space_learning_question",
        type_="foreignkey",
    )
    op.drop_index(
        "ix_space_learning_judgment_misconception_id",
        table_name="space_learning_judgment",
    )
    op.drop_constraint(
        "fk_space_learning_judgment_misconception_id",
        "space_learning_judgment",
        type_="foreignkey",
    )
    op.drop_column("space_learning_judgment", "misconception_id")
    for table in (
        "space_learning_export",
        "space_learning_week",
        "space_learning_advice",
        "space_learning_misconception",
        "space_learning_cluster",
    ):
        op.drop_table(table)
