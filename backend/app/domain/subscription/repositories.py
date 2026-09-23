"""`llm_subscriptions` 的数据访问。

读法就四种，各一个方法：行锁取行（刷新/轮询/撤销这些会改状态的路径都要先锁住，
多副本下串行化 —— 它替代的是 cc-switch 的进程内 mutex）、按 provider 查非终态行、
按 linked_model_name 批量查（模型列表的 overlay 用）、flow 终结时清 flow_* 字段。
"""

import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.subscription.models import LlmSubscription

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
        """行锁取行。会改状态的路径（轮询完成、刷新、撤销）一律走它。"""
        stmt = (
            select(LlmSubscription)
            .where(LlmSubscription.id == subscription_id)
            .with_for_update()
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

    async def live_linked(self) -> Sequence[LlmSubscription]:
        """模型列表 overlay 的读法：挂了模型名、且还在非终态的全部订阅。"""
        stmt = select(LlmSubscription).where(
            LlmSubscription.status.in_(LIVE_STATUSES),
            LlmSubscription.linked_model_name.is_not(None),
        )
        return (await self._session.scalars(stmt)).all()
