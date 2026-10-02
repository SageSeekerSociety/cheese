"""一块空间带一份给 AI 队友的指导默认

#944: `teaching` 这一版多了一层 —— 空间 (`space.teaching`) 上的默认，供整块板的
题目继承。形状与 `space_categories.teaching`（b7e4a1c9d0f2）逐字段相同，因为它就是
同一个键、同一份 `Teaching`、同一个 `resolve`，只是往继承链外多挂一级：空间 →
项目集 → 题目 `protocol_override` → 项目 `settings`，最具体的赢。

只加列、带 server default：已经存在的空间没有这份配置，正是"没设过默认"的意思，
所以不用回填任何一行。

Revision ID: d2a8f04c71e5
Revises: c1f7a09b34d2
Create Date: 2026-10-02
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d2a8f04c71e5"
down_revision: str | Sequence[str] | None = "c1f7a09b34d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "space",
        sa.Column("teaching", sa.JSON(), nullable=False, server_default="{}"),
    )


def downgrade() -> None:
    op.drop_column("space", "teaching")
