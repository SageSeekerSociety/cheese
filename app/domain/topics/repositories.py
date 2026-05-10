import re
from datetime import UTC, datetime

from sqlalchemy import Select, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.topics.models import Topic

# Matches strings that contain at least one letter or digit (ASCII or Unicode).
# Tokens made entirely of emoji / symbols produce empty tsqueries with the
# ``simple`` dictionary and must fall back to ILIKE.
_HAS_WORD_CHAR_RE = re.compile(r"[\w]", re.UNICODE)


def _use_fts(token: str) -> bool:
    """Return True if this token is suitable for PostgreSQL FTS.

    Short tokens (<= 2 chars) and tokens with no word-characters (pure emoji
    / symbols) must fall back to ILIKE because ``plainto_tsquery('simple', ...)``
    would return an empty query for them.
    """
    return len(token) > 2 and _HAS_WORD_CHAR_RE.search(token) is not None


class TopicRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @staticmethod
    def _keyword_filter(token: str):
        """FTS filter for a single keyword token.

        Short tokens or emoji-only tokens fall back to ILIKE; longer word-
        bearing tokens use ``to_tsvector / plainto_tsquery`` with the GIN
        index.
        """
        stripped = token.strip()
        if not _use_fts(stripped):
            like = f"%{stripped}%"
            return Topic.name.ilike(like)
        tsvector = func.to_tsvector(text("'simple'"), func.coalesce(Topic.name, ""))
        tsquery = func.plainto_tsquery(text("'simple'"), stripped)
        return tsvector.op("@@")(tsquery)

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
                base = base.where(self._keyword_filter(token))

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
                    prev_stmt = prev_stmt.where(self._keyword_filter(token))
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
        now = datetime.now(UTC).replace(tzinfo=None)
        topic = Topic(
            name=name,
            created_by_id=created_by_id,
            created_at=now,
            deleted_at=None,
        )
        self._session.add(topic)
        await self._session.flush()
        return topic
