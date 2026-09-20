"""tasks.residency / tasks.queued_at —— 驻留槽随每任务容器一起退休

驻留槽的意思是「一个房间最多同时开 4 条后台子代理，多的排队」，而它成立的前提是
每条活各起一个容器、由平台决定谁现在能跑。任务=分身之后没有这回事了：一条活是房间
自己会话里的一个分身，起几个、什么时候起，是房间在它自己的轮次里决定的，平台既不
排队也不发号。于是这两列的写入方一个不剩——`admit`/`touch`/`release` 全部随槽位机
制删掉——留下来只会是看板上一个永远为假的分支和一个永远是 `idle` 的字段。

`last_turn_at` 留着：它现在的含义是「最后一次有人确认这条活还活着」，认领分身时盖
一次，看板拿它跟这条活最后一个 block 取晚的那个，判断失联。

Revision ID: e4a92b1c7d30
Revises: d7f3a1c05b28
Create Date: 2026-09-06 06:40:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e4a92b1c7d30"
down_revision: str | Sequence[str] | None = "d7f3a1c05b28"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("ix_tasks_room_id_queued_at", table_name="tasks")
    op.drop_index("ix_tasks_room_id_residency", table_name="tasks")
    op.drop_column("tasks", "queued_at")
    op.drop_column("tasks", "residency")


def downgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "tasks",
        sa.Column(
            "residency",
            sa.String(length=16),
            nullable=False,
            server_default="idle",
        ),
    )
    op.create_index("ix_tasks_room_id_residency", "tasks", ["room_id", "residency"])
    op.create_index(
        "ix_tasks_room_id_queued_at",
        "tasks",
        ["room_id", "queued_at"],
        postgresql_where=sa.text("queued_at IS NOT NULL"),
    )
