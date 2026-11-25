"""integrations：一个人在一个团队的飞书应用上只有一个连接

Revision ID: 499940a660b1
Revises: 8a9d4083c7c7
Create Date: 2026-11-25

成员通过团队的应用连的飞书（``provider='feishu'``、``config`` 带 ``team_id`` 的行）只记
``team_id`` 和本人的 token；这个部分唯一索引让两次连接同时插入时后到的撞上它，服务端转成
对先建那一行的重连。建在已有的 ``integrations`` 上，所以 CONCURRENTLY（同
``fbdb95e20845``），单独一个迁移。已有的行没有 ``team_id``，不在索引里。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "499940a660b1"
down_revision: str | Sequence[str] | None = "8a9d4083c7c7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NAME = "uq_integrations_owner_feishu_team"


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.create_index(
            NAME,
            "integrations",
            ["owner_user_id", sa.text("(config ->> 'team_id')")],
            unique=True,
            postgresql_where=sa.text("provider = 'feishu' AND config ? 'team_id'"),
            postgresql_concurrently=True,
            if_not_exists=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(
            NAME,
            table_name="integrations",
            postgresql_concurrently=True,
            if_exists=True,
        )
