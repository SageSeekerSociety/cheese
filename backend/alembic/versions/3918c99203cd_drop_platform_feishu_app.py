"""去掉平台级飞书应用的表 feishu_apps

Revision ID: 3918c99203cd
Revises: 499940a660b1
Create Date: 2026-11-25

企业自建应用只在它自己那家企业的飞书里能用，所以平台不再持有飞书应用：应用设在团队上
（``team_feishu_apps``），成员的连接只记是哪个团队的应用；较早的连接自带 ``config.app_id``
和密封在 ``secret`` 里的 App Secret。上一个发布（#5427）已经不再映射、不再读写
``feishu_apps``，这一步按两次发布的规矩删表：部署时还在服务的上一版不碰它。

先用 ``with_lock_retries`` 拿下这张表（没有指向它、也没有它指向别处的外键），拿不到就放手
稍后再试，不排在活着的请求后面占着锁。表已经不在（中途断掉之后重跑）就直接返回，不去锁一张不存在的表。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "3918c99203cd"
down_revision: str | Sequence[str] | None = "499940a660b1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Already gone (a run cut short after the drop, or dropped by hand): there
    # is nothing to lock, and locking a missing table would fail the upgrade.
    if (
        op.get_bind().execute(sa.text("SELECT to_regclass('feishu_apps')")).scalar()
        is None
    ):
        return
    with_lock_retries("feishu_apps")
    op.execute("DROP TABLE IF EXISTS feishu_apps")


def downgrade() -> None:
    """Puts back an empty table: the platform app's row is not restored (it was
    deleted from production before #5427 shipped, and nothing reads it)."""
    op.create_table(
        "feishu_apps",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("app_id", sa.String(length=64), nullable=False),
        sa.Column(
            "domain", sa.String(length=16), nullable=False, server_default="feishu"
        ),
        sa.Column("secret", sa.Text(), nullable=False),
        sa.Column("updated_by", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
