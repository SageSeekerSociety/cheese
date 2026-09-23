"""`llm_subscriptions` 与 `llm_subscription_models` 的数据访问。

读法就四种，各一个方法：行锁取行（刷新/轮询/撤销这些会改状态的路径都要先锁住，
多副本下串行化 —— 它替代的是 cc-switch 的进程内 mutex）、按 provider 查非终态行、
按上架模型批量查（模型列表的 overlay 用）、flow 终结时清 flow_* 字段。
"""

import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.subscription.models import LlmSubscription, LlmSubscriptionModel

#: 非终态：与 `uq_llm_subscriptions_live_provider` 部分唯一索引同一组值，
#: 两处必须一起改（一个占位的状态不在这里，唯一约束就拦不住它）。
LIVE_STATUSES = ("pending", "active", "refresh_failed", "reauth_required")


class LlmSubscriptionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add(self, row: LlmSubscription) -> LlmSubscription:
        self._session.add(row)
        return row

    async def get(self, subscription_id: uuid.UUID) -> LlmSubscription | None:
        return await self._session.get(LlmSubscription, subscription_id)

    async def get_locked(self, subscription_id: uuid.UUID) -> LlmSubscription | None:
        """行锁取行。会改状态的路径（轮询完成、刷新、撤销）一律走它。

        populate_existing 不能省：调用方往往刚在同一会话里读过这行（后台循环的
        due_for_refresh、路由的预检查），identity map 里躺着锁前快照；不强灌的话
        FOR UPDATE 等锁结束后看到的仍是旧值，并发刷新会拿已轮换的旧 refresh_token
        去打上游、把健康订阅误判成 reauth_required。
        """
        stmt = (
            select(LlmSubscription)
            .where(LlmSubscription.id == subscription_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return await self._session.scalar(stmt)

    async def live_for_provider(self, provider: str) -> LlmSubscription | None:
        stmt = (
            select(LlmSubscription)
            .where(
                LlmSubscription.provider == provider,
                LlmSubscription.status.in_(LIVE_STATUSES),
            )
            .limit(1)
        )
        return await self._session.scalar(stmt)

    async def pendings_for_provider(self, provider: str) -> Sequence[LlmSubscription]:
        """该 provider 所有 `pending` 行：新 flow 开启时把它们顶成 superseded。"""
        stmt = select(LlmSubscription).where(
            LlmSubscription.provider == provider,
            LlmSubscription.status == "pending",
        )
        return (await self._session.scalars(stmt)).all()

    async def list_all(self) -> Sequence[LlmSubscription]:
        stmt = select(LlmSubscription).order_by(LlmSubscription.created_at.desc())
        return (await self._session.scalars(stmt)).all()

    async def due_for_refresh(
        self, statuses: Sequence[str], before: datetime
    ) -> Sequence[LlmSubscription]:
        """后台循环的读法：给定状态集合、且 token 没有到期时间或已在余量内。"""
        stmt = select(LlmSubscription).where(
            LlmSubscription.status.in_(statuses),
            (
                LlmSubscription.token_expires_at.is_(None)
                | (LlmSubscription.token_expires_at <= before)
            ),
        )
        return (await self._session.scalars(stmt)).all()

    async def live_shelved(self) -> Sequence[LlmSubscriptionModel]:
        """模型列表 overlay 的读法：非终态订阅上架的全部模型行（带订阅行）。"""
        stmt = (
            select(LlmSubscriptionModel)
            .join(
                LlmSubscription,
                LlmSubscription.id == LlmSubscriptionModel.subscription_id,
            )
            .where(LlmSubscription.status.in_(LIVE_STATUSES))
            .options(selectinload(LlmSubscriptionModel.subscription))
        )
        return (await self._session.scalars(stmt)).all()

    async def shelved_for(
        self, subscription_id: uuid.UUID
    ) -> Sequence[LlmSubscriptionModel]:
        """一条订阅上架的全部模型行（上架管理的读法与 DTO 都走它）。"""
        stmt = (
            select(LlmSubscriptionModel)
            .where(LlmSubscriptionModel.subscription_id == subscription_id)
            .order_by(LlmSubscriptionModel.created_at)
        )
        return (await self._session.scalars(stmt)).all()

    async def has_other_live_for_model(
        self, model_name: str, exclude_id: uuid.UUID
    ) -> bool:
        """除 exclude_id 外，是否还有非终态订阅上架着同一个网关模型。

        下架/撤销停用模型前用它确认被处理的行真是该模型最后的凭据来源 ——
        重授权后新行还活跃时，处理旧的 superseded 行不该把在服模型停掉。
        """
        stmt = (
            select(LlmSubscriptionModel.id)
            .join(
                LlmSubscription,
                LlmSubscription.id == LlmSubscriptionModel.subscription_id,
            )
            .where(
                LlmSubscriptionModel.name == model_name,
                LlmSubscription.status.in_(LIVE_STATUSES),
                LlmSubscription.id != exclude_id,
            )
            .limit(1)
        )
        return (await self._session.scalar(stmt)) is not None
