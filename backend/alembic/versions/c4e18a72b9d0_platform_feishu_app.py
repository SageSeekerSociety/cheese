"""平台级飞书应用：管理员配一次，成员各自授权

Revision ID: c4e18a72b9d0
Revises: d931a80be823
Create Date: 2026-09-29

成员不再各建一个企业自建应用。这张表只有一行（``id`` 恒为
``FEISHU_APP_ROW_ID``，写在 `domain/integration/models.py` 里），存的是
``app_id`` / ``domain`` 和密封后的 ``app_secret``；成员的行
（``integrations``）只留他自己的 ``user_access_token`` / ``refresh_token``。

**没有数据迁移**：老式连接把 ``app_id`` 和 ``app_secret`` 存在自己那一行里，
照旧按它自带的凭据用（读的那一侧在 ``service.feishu_settings``）。这张表是空的，
只是说明还没有管理员配过平台应用 —— 界面据此把「连接飞书」置灰。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c4e18a72b9d0"
down_revision: str | Sequence[str] | None = "d931a80be823"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
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


def downgrade() -> None:
    op.drop_table("feishu_apps")
