"""去掉平台级飞书应用：每个飞书连接都用成员自己公司的应用

Revision ID: 76fa3f37bf12
Revises: c5e1a7d39f20
Create Date: 2026-11-24

企业自建应用只在它自己那家企业的飞书里能用，平台配的那一个应用连不了别家公司的账号，
所以平台不再持有飞书应用：每个飞书连接（``integrations`` 里 ``provider='feishu'`` 的行）
自己带 ``config.app_id`` / ``config.domain`` 和密封在 ``secret`` 里的 App Secret。

**没有数据迁移**：删表时生产上没有一行飞书连接依赖平台应用（唯一一行自带 ``app_id``）。
平台应用那一行本身随表删掉；回滚只建回空表，不恢复它。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "76fa3f37bf12"
down_revision: str | Sequence[str] | None = "c5e1a7d39f20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # migration-safety: allow drop-table — the release this deploy replaces reads feishu_apps only on the Feishu connect endpoints and the admin 「飞书应用」 page, and no feishu integration row depends on it; during the deploy window those requests fail instead of serving a platform app this release removes
    op.drop_table("feishu_apps")


def downgrade() -> None:
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
