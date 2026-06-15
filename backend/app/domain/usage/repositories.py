"""Resource usage data access + aggregation."""

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.usage.models import ResourceUsage


class UsageRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def add(
        self,
        *,
        project_id: uuid.UUID,
        topic_id: uuid.UUID | None,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float,
        kind: str = "chat",
    ) -> ResourceUsage:
        row = ResourceUsage(
            project_id=project_id,
            topic_id=topic_id,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
            cost_usd=cost_usd,
            kind=kind,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def _agg(self, column, value) -> dict:
        stmt = select(
            func.coalesce(func.sum(ResourceUsage.input_tokens), 0),
            func.coalesce(func.sum(ResourceUsage.output_tokens), 0),
            func.coalesce(func.sum(ResourceUsage.total_tokens), 0),
            func.coalesce(func.sum(ResourceUsage.cost_usd), 0.0),
            func.count(),
        ).where(column == value)
        row = (await self._session.execute(stmt)).one()
        return {
            "input_tokens": int(row[0]),
            "output_tokens": int(row[1]),
            "total_tokens": int(row[2]),
            "cost_usd": float(row[3]),
            "turns": int(row[4]),
        }

    async def for_topic(self, topic_id: uuid.UUID) -> dict:
        return await self._agg(ResourceUsage.topic_id, topic_id)

    async def for_project(self, project_id: uuid.UUID) -> dict:
        return await self._agg(ResourceUsage.project_id, project_id)
