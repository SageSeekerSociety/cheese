"""tasks.subagent_id —— 一条活由房间里的哪个分身在做

一个 Claude 会话里可以同时跑好几个分身，它们的 hook 事件和房间自己的走同一条管子，
唯一能区分的就是事件上带的 ``agent_id``（房间自己的事件根本没有这个键）。所以这一列
就是全部的归属依据：没有它，每个分身的工具调用都记在房间头上，房间的时间线变成一条
谁也认不出的混合流。

字符串而不是外键：这个 id 是容器里的 Claude Code 自己生成的，平台只负责认。NULL =
还没有分身认领这条活——任务行在派活那一刻就存在，绑定发生在房间真的起了一个分身之后。

索引是 (room_id, subagent_id)：这个查询按每一条 hook 事件跑，不是按每次开页面跑。

Revision ID: d7f3a1c05b28
Revises: b3f1c7d9a204
Create Date: 2026-09-06 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d7f3a1c05b28"
down_revision: str | Sequence[str] | None = "c8d3f61a9e42"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column("subagent_id", sa.String(length=64), nullable=True),
    )
    op.create_index("ix_tasks_room_id_subagent_id", "tasks", ["room_id", "subagent_id"])


def downgrade() -> None:
    op.drop_index("ix_tasks_room_id_subagent_id", table_name="tasks")
    op.drop_column("tasks", "subagent_id")
