"""agent_instances.is_active —— 「停用」不是删除

一个 agent 的记忆池按 ``handle`` 归档，房间指向的是它的 id，所以删掉这一行会同时
抹掉它学过的一切、并让正在用它的房间指向空。停用要的从来不是这个：已经在用它的
房间照常工作，记忆原样留着，它只是不再出现在「交给谁」的候选里。所以退役是这一列
上的一个 false，而不是一次 DELETE。

server_default 是 true：既有的 agent 一个都没被停用过。

Revision ID: b3f1c7d9a204
Revises: 89fb9b9a11e0
Create Date: 2026-09-01 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b3f1c7d9a204"
down_revision: str | Sequence[str] | None = "89fb9b9a11e0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_instances",
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )


def downgrade() -> None:
    op.drop_column("agent_instances", "is_active")
