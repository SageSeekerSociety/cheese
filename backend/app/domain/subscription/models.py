"""`llm_subscriptions`：一条平台级 LLM 订阅的全部状态。

新风格表（UuidPk + Timestamps，见 `app/domain/common.py`）。VARCHAR 枚举照
feedback 先例不用 PG enum（`app/domain/feedback/models.py`）；操作者存 handle
快照（同一惯例）。

状态机（`status`，VARCHAR(16) 全放得下）：

- `pending`：device flow 进行中（flow_* 字段有值）。
- `active`：token 在库、已挂网关模型。
- `flow_expired`：flow 过期，终态。
- `superseded`：被新 flow 顶掉或被取消，终态。
- `refresh_failed`：token 已过期且刷新连续失败，流量将 401。
- `reauth_required`：refresh_token 被判死，要重新授权；经定向 device flow 验证
  同身份后回到 `active`。
- `revoked`：管理员移除，终态。

凭据三件套（access / refresh / id token）只存 Fernet 密文（
`app.core.crypto.encrypt_text`，照 `f9a1c7e3b502` 迁移的先例），**绝不进任何
API 响应**——DTO 在 `services.py` 里脱敏。
"""

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import JSON, DateTime, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.domain.common import Timestamps, UuidPk


class LlmSubscription(UuidPk, Timestamps, Base):
    __tablename__ = "llm_subscriptions"
    __table_args__ = (
        # 管理页列表按时间倒序读（照 d4c1a7f83b96 的样板）。
        Index("ix_llm_subscriptions_created_at", sa.text("created_at DESC")),
        # 「一座一订阅」的库层强制：每个 provider 至多一条非终态行。
        Index(
            "uq_llm_subscriptions_live_provider",
            "provider",
            unique=True,
            postgresql_where=sa.text(
                "status IN ('pending','active','refresh_failed','reauth_required')"
            ),
        ),
    )

    # v1 仅 'openai_codex'。
    provider: Mapped[str] = mapped_column(String(32))
    label: Mapped[str] = mapped_column(String(200), default="")
    status: Mapped[str] = mapped_column(String(16))

    # 账号元数据（admin 才可见；email 是 PII，仅存这一处，不进审计 detail）。
    account_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    chatgpt_account_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # 重新授权时校验同一身份用。
    id_token_subject: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # 凭据：Fernet 密文。绝不进 API 响应；pending 期为空。
    access_token_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    refresh_token_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    id_token_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # 刷新可观测（补 gateway_admin_audit 不覆盖自动动作的空缺）。
    last_refresh_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_refresh_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 额度快照：最近一次 wham/usage 的解析结果；v1 只存最近一份，不建历史表。
    quota_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    quota_fetched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # 这条订阅喂给网关的哪条运行时模型。
    linked_model_name: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # device flow 进行中状态（完成/终结后清空）。
    flow_device_auth_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    flow_user_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    flow_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    flow_last_poll_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # 定向重授权的旧行 id。
    flow_target_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid, nullable=True)

    created_by_handle: Mapped[str] = mapped_column(String(64), default="")
