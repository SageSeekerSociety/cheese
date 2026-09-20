"""一张卡没有自己的 agent

Revision ID: c8f21d4a7e93
Revises: a1c4e7b09f36
Create Date: 2026-09-06 20:40:00.000000

`tasks.agent_instance_id` 是「活还是一个地点」时代最后一根柱子：那时一条活有自己
的会话，所以要记它归哪个 agent。现在做一条活的是房间会话里的一个分身——会话是
房间的，agent 也就是房间的，卡上记的是哪个分身在做（`subagent_id`），不是哪个
agent。留着这一列意味着有第二个身份可以和房间的不一致，而没有任何东西会去读它。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c8f21d4a7e93"
down_revision: str | Sequence[str] | None = "a1c4e7b09f36"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("ix_tasks_agent_instance_id", table_name="tasks")
    op.drop_column("tasks", "agent_instance_id")


def downgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column("agent_instance_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_tasks_agent_instance_id",
        "tasks",
        "agent_instances",
        ["agent_instance_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_tasks_agent_instance_id", "tasks", ["agent_instance_id"], unique=False
    )
