"""一条记忆 = 一个 markdown 文件

Revision ID: c1a7e4d29b58
Revises: 7c2e91a4d3f6
Create Date: 2026-09-26
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c1a7e4d29b58"
down_revision: str | Sequence[str] | None = "7c2e91a4d3f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema.

    新的记忆存储（照搬 Claude Code 的 memory 机制）：一条记忆一个 markdown 文件，
    按项目 + 作用域 + 路径定位，`version` 是回写时的乐观锁。

    旧表 `memory_entries` **一行都不动**：其中每一条的去向要逐条判定（迁移报告，
    另一次任务），这里先加新表不动旧表——两边同时存在比先删后补安全，而旧表的
    读路径在这次之后只剩「还没搬走的那些」，不会和新表抢同一件事。

    ``owner_handle`` 对 team 记忆存空串而不是 NULL：唯一约束在 SQL 里 NULL !=
    NULL，可空会让同一个项目的 team 记忆反复插进去。见
    `app/domain/memory/models.py` 的 `MemoryFileRecord`。
    """
    op.create_table(
        "memory_files",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column(
            "scope",
            sa.Enum("team", "private", native_enum=False, length=8),
            nullable=False,
        ),
        sa.Column(
            "owner_handle", sa.String(length=64), nullable=False, server_default=""
        ),
        sa.Column("path", sa.String(length=200), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "updated_by", sa.String(length=64), nullable=False, server_default=""
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    # 「先查重，再新建」在数据库那一侧的样子：一个作用域里同一个路径只许有一行。
    op.create_index(
        "uq_memory_files_scope_path",
        "memory_files",
        ["project_id", "scope", "owner_handle", "path"],
        unique=True,
    )
    op.create_index("ix_memory_files_project_id", "memory_files", ["project_id"])
    op.create_index("ix_memory_files_scope", "memory_files", ["scope"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_memory_files_scope", table_name="memory_files")
    op.drop_index("ix_memory_files_project_id", table_name="memory_files")
    op.drop_index("uq_memory_files_scope_path", table_name="memory_files")
    op.drop_table("memory_files")
