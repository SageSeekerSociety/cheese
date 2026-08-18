"""carry the 机构协议 on the 赛题 hierarchy

#370: the protocol (资源包 / 条件 / 默认专家角色) lived on cheesex's
`task_templates`, a parallel 题目 hierarchy with no UI to create it. The 知是
side already had every level — 项目集 (`space_categories`) → 赛题 (`task`) →
领取 (`task_membership`) — and was missing only these four fields.

Placement is #370 option (c), decided 2026-08-17: the terms sit on the 项目集,
because 创研课 2026 秋 has twenty 赛题 and one set of terms, with a per-赛题
`protocol_override` for the case that genuinely differs.

Additive only. Every column is nullable or carries a server default, so existing
rows keep working and nothing has to be backfilled: no protocol is exactly what
an unlinked project already had (spec §4 项目自治).

Revision ID: a1f4c73b5e28
Revises: c1a5f70b3d24
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a1f4c73b5e28"
down_revision: str | Sequence[str] | None = "c1a5f70b3d24"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "space_categories",
        sa.Column("resource_pack", sa.JSON(), nullable=False, server_default="{}"),
    )
    op.add_column(
        "space_categories",
        sa.Column("conditions", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.add_column(
        "space_categories", sa.Column("default_role", sa.String(64), nullable=True)
    )
    op.add_column("task", sa.Column("protocol_override", sa.JSON(), nullable=True))
    op.add_column(
        "task_membership",
        sa.Column("pitch", sa.Text(), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("task_membership", "pitch")
    op.drop_column("task", "protocol_override")
    op.drop_column("space_categories", "default_role")
    op.drop_column("space_categories", "conditions")
    op.drop_column("space_categories", "resource_pack")
