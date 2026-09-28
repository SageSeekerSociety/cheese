"""旧表迁移的计划与报告

Revision ID: e7c2a9d41f58
Revises: d4b8f1a20c37
Create Date: 2026-09-27
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e7c2a9d41f58"
down_revision: str | Sequence[str] | None = "d4b8f1a20c37"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema.

    迁移是「先报告、人点头、再写」（见 `domain/memory/migration.py`），所以那份
    报告必须有一个落脚处，而且它得和复核动作绑在一起：

    - ``report`` 与 ``decisions``：dry-run 出来的那一份。apply 重放它，不再问一次
      模型——人复核的是这一份，不是模型的下一次回答。
    - ``sources`` / ``sources_digest``：报告描述的是旧表**当时**的样子。落笔前对一
      次指纹，对不上就说明旧表变了，重跑。
    - ``approved_by`` / ``approved_at``：谁点的头。这一列是这次迁移唯一的闸。
    - ``source_ids``：搬过哪些。第二次 dry-run 不再搬它们。
    """
    op.create_table(
        "memory_migration_plans",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column(
            "status", sa.String(length=16), nullable=False, server_default="draft"
        ),
        sa.Column(
            "sources_digest", sa.String(length=32), nullable=False, server_default=""
        ),
        sa.Column("report", sa.Text(), nullable=False, server_default=""),
        sa.Column("sources", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("source_ids", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("decisions", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("files", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("indexes", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("suggestions", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column(
            "created_by", sa.String(length=64), nullable=False, server_default=""
        ),
        sa.Column(
            "approved_by", sa.String(length=64), nullable=False, server_default=""
        ),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_memory_migration_plans_project_id", "memory_migration_plans", ["project_id"]
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        "ix_memory_migration_plans_project_id", table_name="memory_migration_plans"
    )
    op.drop_table("memory_migration_plans")
