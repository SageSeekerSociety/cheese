"""what a delivery hands over

Revision ID: d3f81a6b27c4
Revises: c8a1d5e73f20
Create Date: 2026-09-20 21:00:00

一版是一次交付（#1085 结论五），所以这一版交出去的是什么记在那张卡上，而不是产物
那一行上 —— 那一行只有名字。三种交法：一份文件、一个地址、一次合并。

字节不进库：成品是从源构建出来的，进 git 就是把五十版 20MB 的幻灯片提交进仓库那条
老路；也不在要下载时重建一次 —— 半年后依赖变了，重建出来的和当时交出去的不是同一
份东西。建卡那一刻落一份快照，位置由 project_id 加卡 id 推出来。

`deliverable_kind` 留空不是 `merge`：没声明过和「交出去的是这次合并」是两件不同的
事，页面上要说的话也不同。这一列之前递的卡因此都是 NULL，那几版没有留存文件，而这
补不回来 —— 那份构建产物只活在当时那个工作目录里。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d3f81a6b27c4"
down_revision: str | Sequence[str] | None = "c8a1d5e73f20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "accept_cards",
        sa.Column("deliverable_kind", sa.String(length=16), nullable=True),
    )
    op.add_column(
        "accept_cards",
        sa.Column("deliverable_name", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "accept_cards",
        sa.Column("deliverable_url", sa.String(length=1024), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("accept_cards", "deliverable_url")
    op.drop_column("accept_cards", "deliverable_name")
    op.drop_column("accept_cards", "deliverable_kind")
