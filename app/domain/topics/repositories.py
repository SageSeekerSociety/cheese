from datetime import datetime, timezone

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.topics.models import Topic


class TopicRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_topics_cursor(
        self,
        *,
        keyword: str | None = None,
        page_start: int | None = None,
        page_size: int = 20,
    ) -> tuple[list[Topic], int | None, bool, int | None]:
        base = select(Topic).where(Topic.deleted_at.is_(None))

        if keyword:
            tokens = keyword.strip().split()
            for token in tokens:
                like = f"%{token}%"
                base = base.where(Topic.name.ilike(like))

        base = base.order_by(Topic.id.asc())

        if page_start is not None:
            base = base.where(Topic.id >= page_start)

        stmt = base.limit(page_size + 1)
        result = await self._session.execute(stmt)
        rows = list(result.scalars().all())

        has_more = len(rows) > page_size
        if has_more:
            rows = rows[:page_size]

        next_id = rows[-1].id + 1 if has_more and rows else None

        prev_id = None
        if page_start is not None and rows:
            prev_stmt = (
                select(Topic.id).where(Topic.deleted_at.is_(None)).where(Topic.id < page_start)
            )
            if keyword:
                tokens = keyword.strip().split()
                for token in tokens:
                    like = f"%{token}%"
                    prev_stmt = prev_stmt.where(Topic.name.ilike(like))
            prev_stmt = prev_stmt.order_by(Topic.id.desc()).limit(page_size)
            prev_result = await self._session.execute(prev_stmt)
            prev_ids = list(prev_result.scalars().all())
            if prev_ids:
                prev_id = prev_ids[-1]

        return rows, prev_id, has_more, next_id

    async def get_by_id(self, topic_id: int) -> Topic | None:
        stmt: Select[tuple[Topic]] = select(Topic).where(
            Topic.id == topic_id,
            Topic.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_name(self, name: str) -> Topic | None:
        stmt: Select[tuple[Topic]] = select(Topic).where(
            Topic.name == name,
            Topic.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def create(self, *, name: str, created_by_id: int) -> Topic:
        now = datetime.now(timezone.utc)
        topic = Topic(
            name=name,
            created_by_id=created_by_id,
            created_at=now,
            deleted_at=None,
        )
        self._session.add(topic)
        await self._session.flush()
        return topic
