"""平台级 LLM 订阅表（ChatGPT 订阅导入）。

`llm_subscriptions` 是「一座一订阅」的平台级凭据的家：管理员把一个 ChatGPT
订阅经 device flow 导入后，它的 OAuth token 生命周期（加密落库、后台刷新、刷新
后推进网关运行时模型）全部由平台侧接管。模型见
`app/domain/subscription/models.py::LlmSubscription`。

为什么不复用 `user_o_auth_connection`：那张表的语义是「一个用户的登录连接」
（`user_id` 非空，provider 是 github/google/ruc），而且
`platform_stats/integrations.py` 把它当「登录连接健康度」统计 —— 把平台级共享
订阅塞进去，既语义错位、又污染 oauth_health 的过期口径。所以单独立表。

那张部分唯一索引（`uq_llm_subscriptions_live_provider`）是「一座一订阅」的库层
强制：每个 provider 至多一条非终态（pending/active/refresh_failed/
reauth_required）行，终态行（flow_expired/superseded/revoked）不占位。并发开两个
导入流程时，第二个 `INSERT` 直接撞唯一冲突，而不是落出两条活跃订阅再靠应用层
事后收拾。

Revision ID: eeebfb49d703
Revises: 514d7c9cb013
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "eeebfb49d703"
down_revision: str | Sequence[str] | None = "514d7c9cb013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "llm_subscriptions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("label", sa.String(200), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("account_email", sa.String(320), nullable=True),
        sa.Column("chatgpt_account_id", sa.String(128), nullable=True),
        sa.Column("id_token_subject", sa.String(128), nullable=True),
        sa.Column("access_token_enc", sa.Text(), nullable=True),
        sa.Column("refresh_token_enc", sa.Text(), nullable=True),
        sa.Column("id_token_enc", sa.Text(), nullable=True),
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_refresh_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_refresh_error", sa.Text(), nullable=True),
        sa.Column("quota_snapshot", sa.JSON(), nullable=True),
        sa.Column("quota_fetched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("linked_model_name", sa.String(64), nullable=True),
        sa.Column("flow_device_auth_id", sa.String(128), nullable=True),
        sa.Column("flow_user_code", sa.String(32), nullable=True),
        sa.Column("flow_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("flow_last_poll_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("flow_target_id", sa.Uuid(), nullable=True),
        sa.Column("created_by_handle", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    # 管理页列表按时间倒序读，索引照这个读法建成倒序（d4c1a7f83b96 的样板）。
    op.create_index(
        "ix_llm_subscriptions_created_at",
        "llm_subscriptions",
        [sa.text("created_at DESC")],
    )
    op.create_index(
        "uq_llm_subscriptions_live_provider",
        "llm_subscriptions",
        ["provider"],
        unique=True,
        postgresql_where=sa.text(
            "status IN ('pending','active','refresh_failed','reauth_required')"
        ),
    )


def downgrade() -> None:
    op.drop_index("uq_llm_subscriptions_live_provider", table_name="llm_subscriptions")
    op.drop_index("ix_llm_subscriptions_created_at", table_name="llm_subscriptions")
    op.drop_table("llm_subscriptions")
