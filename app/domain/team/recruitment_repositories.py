import re
from datetime import UTC, datetime

from sqlalchemy import Select, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.team.models import RecruitmentStatus, TeamRecruitmentPost

_HAS_WORD_CHAR_RE = re.compile(r"[\w]", re.UNICODE)


def _use_fts(token: str) -> bool:
    """Return True when *token* is suitable for PostgreSQL FTS."""
    return len(token) > 2 and _HAS_WORD_CHAR_RE.search(token) is not None


class RecruitmentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        team_id: int,
        title: str,
        content: str,
        contact: str | None,
        max_members: int | None,
        created_by: int,
        expires_at: datetime | None,
    ) -> TeamRecruitmentPost:
        now = datetime.now(UTC).replace(tzinfo=None)
        post = TeamRecruitmentPost(
            team_id=team_id,
            title=title,
            content=content,
            contact=contact,
            max_members=max_members,
            status=RecruitmentStatus.OPEN.value,
            created_by=created_by,
            created_at=now,
            updated_at=now,
            expires_at=expires_at,
        )
        self._session.add(post)
        await self._session.flush()
        return post

    async def get_by_id(self, post_id: int) -> TeamRecruitmentPost | None:
        stmt: Select[tuple[TeamRecruitmentPost]] = select(TeamRecruitmentPost).where(
            TeamRecruitmentPost.id == post_id,
            TeamRecruitmentPost.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_open(
        self,
        *,
        page_size: int = 20,
        page_start: int | None = None,
        keyword: str | None = None,
    ) -> tuple[list[TeamRecruitmentPost], bool, int | None]:
        """List OPEN recruitment posts ordered by created_at desc.

        Returns (rows, has_more, next_start_id).
        """
        base = select(TeamRecruitmentPost).where(
            TeamRecruitmentPost.status == RecruitmentStatus.OPEN.value,
            TeamRecruitmentPost.deleted_at.is_(None),
        )
        if keyword:
            base = base.where(self._keyword_filter(keyword))
        base = base.order_by(TeamRecruitmentPost.created_at.desc())
        if page_start is not None:
            base = base.where(TeamRecruitmentPost.id <= page_start)
        stmt = base.limit(page_size + 1)
        result = await self._session.execute(stmt)
        rows = list(result.scalars().all())
        has_more = len(rows) > page_size
        if has_more:
            rows = rows[:page_size]
        next_id = rows[-1].id - 1 if has_more and rows else None
        return rows, has_more, next_id

    async def list_by_team(self, team_id: int) -> list[TeamRecruitmentPost]:
        stmt = (
            select(TeamRecruitmentPost)
            .where(
                TeamRecruitmentPost.team_id == team_id,
                TeamRecruitmentPost.deleted_at.is_(None),
            )
            .order_by(TeamRecruitmentPost.created_at.desc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def update(
        self,
        post: TeamRecruitmentPost,
        *,
        title: str | None = None,
        content: str | None = None,
        contact: str | None = ...,  # type: ignore[assignment]
        max_members: int | None = ...,  # type: ignore[assignment]
        status: str | None = None,
        expires_at: datetime | None = ...,  # type: ignore[assignment]
    ) -> TeamRecruitmentPost:
        if title is not None:
            post.title = title
        if content is not None:
            post.content = content
        if contact is not ...:  # type: ignore[comparison-overlap]
            post.contact = contact
        if max_members is not ...:  # type: ignore[comparison-overlap]
            post.max_members = max_members
        if status is not None:
            post.status = status
        if expires_at is not ...:  # type: ignore[comparison-overlap]
            post.expires_at = expires_at
        post.updated_at = datetime.now(UTC).replace(tzinfo=None)
        await self._session.flush()
        return post

    async def soft_delete(self, post: TeamRecruitmentPost) -> None:
        post.deleted_at = datetime.now(UTC).replace(tzinfo=None)
        await self._session.flush()

    @staticmethod
    def _keyword_filter(keyword: str):
        """FTS filter for recruitment post title + content."""
        stripped = keyword.strip()
        if not _use_fts(stripped):
            like = f"%{stripped}%"
            return or_(
                TeamRecruitmentPost.title.ilike(like),
                TeamRecruitmentPost.content.ilike(like),
            )
        tsvector = func.to_tsvector(
            text("'simple'"),
            func.coalesce(TeamRecruitmentPost.title, "")
            + " "
            + func.coalesce(TeamRecruitmentPost.content, ""),
        )
        tsquery = func.plainto_tsquery(text("'simple'"), stripped)
        return tsvector.op("@@")(tsquery)
