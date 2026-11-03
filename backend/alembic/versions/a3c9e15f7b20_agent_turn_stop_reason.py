"""agent_turns.stop_reason —— 谁让这一轮停的，不只是什么时候停的

2026-10-22 的一轮开了 13 小时：17:46 开工，中间一直有动作，直到 06:47 才结束。
事后只能问出「它停了」，问不出「是谁让它停的」——是会话自己收的尾，还是孤儿
清扫收的，还是任务被关掉时带走的。时间戳能回答「什么时候」，回答不了这个。

这一列由关掉它的那段代码写下（``models.STOP_*``），NULL 表示这一列加上之前就
结束的那些行。

Revision ID: a3c9e15f7b20
Revises: e73ca7b0dd69
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a3c9e15f7b20"
down_revision: str | Sequence[str] | None = "e73ca7b0dd69"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_turns",
        sa.Column("stop_reason", sa.String(length=32), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("agent_turns", "stop_reason")
