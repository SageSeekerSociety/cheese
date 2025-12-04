from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.llm.models import AIUserQuota


class AIUserQuotaRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_or_create(self, user_id: int, daily_total: float) -> AIUserQuota:
        stmt = select(AIUserQuota).where(AIUserQuota.user_id == user_id)
        result = await self._session.execute(stmt)
        entity = result.scalar_one_or_none()
        now = datetime.now(timezone.utc)
        if entity is None:
            reset_at = self._next_reset(now)
            entity = AIUserQuota(user_id=user_id, used=0.0, reset_at=reset_at)
            self._session.add(entity)
            await self._session.flush()
            return entity
        if now >= entity.reset_at:
            entity.used = 0.0
            entity.reset_at = self._next_reset(now)
            await self._session.flush()
        return entity

    async def consume(self, user_id: int, amount: float, daily_total: float) -> tuple[float, datetime]:
        entity = await self.get_or_create(user_id, daily_total)
        if entity.used + amount > daily_total:
            raise ValueError("AI quota exhausted")
        entity.used += amount
        await self._session.flush()
        remaining = daily_total - entity.used
        return remaining, entity.reset_at

    async def get_quota(self, user_id: int, daily_total: float) -> tuple[float, datetime]:
        entity = await self.get_or_create(user_id, daily_total)
        remaining = max(0.0, daily_total - entity.used)
        return remaining, entity.reset_at

    def _next_reset(self, now: datetime) -> datetime:
        tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        return tomorrow
