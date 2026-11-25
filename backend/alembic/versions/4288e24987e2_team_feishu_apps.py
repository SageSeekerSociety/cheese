"""飞书应用设在团队上：team_feishu_apps，和每个成员每个团队一个飞书连接

Revision ID: 4288e24987e2
Revises: fbdb95e20845
Create Date: 2026-11-24

企业自建应用只在它那家企业的飞书里能用，团队是持有它的那一层：公司的团队设公司的应用，
一个人自己用就设在他的个人团队上。``team_feishu_apps`` 每个团队至多一行（主键就是
``team_id``），App Secret 密封、绑在团队上。成员通过团队的应用连的飞书（``integrations``
里 ``provider='feishu'``、``config`` 带 ``team_id`` 的行）只记 ``team_id`` 和本人的 token；
``uq_integrations_owner_feishu_team`` 让一个人在一个团队上只有一行，两次连接同时插入时
后到的撞上索引，服务端转成对先建那一行的重连。

**不动已有的行。** 原来自带 ``app_id`` / ``app_secret`` 的连接、平台应用时代 ``config``
为空的连接都原样留着：新代码照它们本来的样子读（前者照旧能用、提示主人用团队的应用重新
连接；后者显示需要重新连接、续期跳过它）。所以部署窗口里还在服务的上一版、以及回退到的
那一版，读到的每一行都和它写下时一样。

索引建在已有的 ``integrations`` 上，所以 CONCURRENTLY（同 ``fbdb95e20845``）；新表随它
一起建，表上没有数据，不需要。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "4288e24987e2"
down_revision: str | Sequence[str] | None = "fbdb95e20845"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TEAM_INDEX = "uq_integrations_owner_feishu_team"


def upgrade() -> None:
    op.create_table(
        "team_feishu_apps",
        sa.Column("team_id", sa.BigInteger(), nullable=False),
        sa.Column("app_id", sa.String(length=64), nullable=False),
        sa.Column("domain", sa.String(length=16), nullable=False),
        sa.Column("secret", sa.Text(), nullable=False),
        sa.Column("updated_by", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["team_id"],
            ["team.id"],
            name="fk_team_feishu_apps_team_id_team",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("team_id"),
    )
    with op.get_context().autocommit_block():
        op.create_index(
            TEAM_INDEX,
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
            TEAM_INDEX,
            table_name="integrations",
            postgresql_concurrently=True,
            if_exists=True,
        )
    op.drop_table("team_feishu_apps")
