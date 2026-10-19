from collections.abc import Sequence
from datetime import UTC, datetime
from typing import TypedDict

from sqlalchemy import Select, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.answers.models import Answer, AnswerFavorite, AnswerQueryLog
from app.domain.discussion.models import DiscussableModelType, Discussion
from app.domain.questions.models import Attitude, VoteType


class AnswerStats(TypedDict):
    """The per-answer counters a list row shows, already resolved for a viewer."""

    positive_count: int
    negative_count: int
    user_vote: str | None
    favorite_count: int
    is_favorite: bool
    comment_count: int
    view_count: int


class AnswerRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_answer(
        self,
        *,
        question_id: int,
        created_by_id: int,
        content: str,
    ) -> Answer:
        now = datetime.now(UTC)
        answer = Answer(
            question_id=question_id,
            created_by_id=created_by_id,
            content=content,
            created_at=now,
            updated_at=now,
            deleted_at=None,
        )
        self._session.add(answer)
        await self._session.flush()
        return answer

    async def list_answers_for_question(
        self,
        *,
        question_id: int,
        limit: int,
        offset: int = 0,
        cursor_id: int | None = None,
    ) -> Sequence[Answer]:
        stmt: Select[tuple[Answer]] = select(Answer).where(
            Answer.question_id == question_id,
            Answer.deleted_at.is_(None),
        )
        if cursor_id is not None:
            stmt = stmt.where(Answer.id >= cursor_id)
        stmt = stmt.order_by(Answer.id.asc()).limit(limit)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list_all_answer_ids_for_question(self, question_id: int) -> list[int]:
        stmt = (
            select(Answer.id)
            .where(
                Answer.question_id == question_id,
                Answer.deleted_at.is_(None),
            )
            .order_by(Answer.id.asc())
        )
        result = await self._session.execute(stmt)
        return [r[0] for r in result.all()]

    async def count_answers_for_question(self, question_id: int) -> int:
        stmt = select(func.count(Answer.id)).where(
            Answer.question_id == question_id,
            Answer.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return int(result.scalar_one() or 0)

    async def get_by_id(self, answer_id: int) -> Answer | None:
        stmt: Select[tuple[Answer]] = select(Answer).where(
            Answer.id == answer_id,
            Answer.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def vote(self, *, answer_id: int, user_id: int, vote_type: str) -> Attitude:
        existing = await self._get_vote(answer_id, user_id)
        now = datetime.now(UTC)
        if existing is not None:
            existing.attitude = vote_type
            existing.updated_at = now
            await self._session.flush()
            return existing
        attitude = Attitude(
            attitudable_id=answer_id,
            attitudable_type="ANSWER",
            user_id=user_id,
            attitude=vote_type,
            created_at=now,
            updated_at=now,
        )
        self._session.add(attitude)
        await self._session.flush()
        return attitude

    async def remove_vote(self, *, answer_id: int, user_id: int) -> bool:
        existing = await self._get_vote(answer_id, user_id)
        if existing is None:
            return False
        await self._session.delete(existing)
        await self._session.flush()
        return True

    async def _get_vote(self, answer_id: int, user_id: int) -> Attitude | None:
        stmt: Select[tuple[Attitude]] = select(Attitude).where(
            Attitude.attitudable_id == answer_id,
            Attitude.attitudable_type == "ANSWER",
            Attitude.user_id == user_id,
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_user_vote(self, answer_id: int, user_id: int) -> str | None:
        vote = await self._get_vote(answer_id, user_id)
        return vote.attitude if vote else None

    async def count_votes(self, answer_id: int) -> dict[str, int]:
        stmt = (
            select(Attitude.attitude, func.count(Attitude.id))
            .where(
                Attitude.attitudable_id == answer_id,
                Attitude.attitudable_type == "ANSWER",
            )
            .group_by(Attitude.attitude)
        )
        result = await self._session.execute(stmt)
        counts = {VoteType.POSITIVE.value: 0, VoteType.NEGATIVE.value: 0}
        for attitude, count in result.all():
            counts[attitude] = count
        return counts

    async def update_answer(
        self, answer: Answer, *, content: str | None = None
    ) -> Answer:
        if content is not None:
            answer.content = content
        answer.updated_at = datetime.now(UTC)
        await self._session.flush()
        return answer

    async def soft_delete(self, answer: Answer) -> None:
        answer.deleted_at = datetime.now(UTC)
        await self._session.flush()

    async def add_favorite(self, *, answer_id: int, user_id: int) -> bool:
        existing = await self._get_favorite(answer_id, user_id)
        if existing is not None:
            return False
        fav = AnswerFavorite(
            answer_id=answer_id,
            user_id=user_id,
        )
        self._session.add(fav)
        await self._session.flush()
        return True

    async def remove_favorite(self, *, answer_id: int, user_id: int) -> bool:
        fav = await self._get_favorite(answer_id, user_id)
        if fav is None:
            return False
        await self._session.delete(fav)
        await self._session.flush()
        return True

    async def _get_favorite(
        self, answer_id: int, user_id: int
    ) -> AnswerFavorite | None:
        stmt: Select[tuple[AnswerFavorite]] = select(AnswerFavorite).where(
            AnswerFavorite.answer_id == answer_id,
            AnswerFavorite.user_id == user_id,
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def is_favorited(self, answer_id: int, user_id: int) -> bool:
        fav = await self._get_favorite(answer_id, user_id)
        return fav is not None

    async def count_favorites(self, answer_id: int) -> int:
        stmt = (
            select(func.count())
            .select_from(AnswerFavorite)
            .where(
                AnswerFavorite.answer_id == answer_id,
            )
        )
        result = await self._session.execute(stmt)
        return int(result.scalar_one() or 0)

    async def log_view(
        self, *, answer_id: int, viewer_id: int | None, ip: str, user_agent: str | None
    ) -> None:
        log = AnswerQueryLog(
            answer_id=answer_id,
            viewer_id=viewer_id,
            ip=ip,
            user_agent=user_agent,
            created_at=datetime.now(UTC),
        )
        self._session.add(log)
        await self._session.flush()

    async def count_views(self, answer_id: int) -> int:
        stmt = select(func.count(AnswerQueryLog.id)).where(
            AnswerQueryLog.answer_id == answer_id,
        )
        result = await self._session.execute(stmt)
        return int(result.scalar_one() or 0)

    async def count_comments(self, answer_id: int) -> int:

        stmt = select(func.count(Discussion.id)).where(
            Discussion.model_type == DiscussableModelType.ANSWER.value,
            Discussion.model_id == answer_id,
            Discussion.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return int(result.scalar_one() or 0)

    async def answer_stats_for_ids(
        self,
        *,
        answer_ids: Sequence[int],
        viewer_id: int | None = None,
    ) -> dict[int, AnswerStats]:
        """Every stat an answer list row shows, for the whole page in one query.

        The list used to ask six times per row — vote counts, the viewer's vote,
        favourites, the viewer's favourite, comments and views. At the maximum
        page of 100 that is 600 round trips for one response. Each source is
        aggregated per ``answer_id`` in a subquery and the four are joined onto
        the requested ids once, so the round-trip count does not grow with the
        page.

        ``viewer_id`` is ``None`` for an anonymous reader (or a seat without a
        user id): the viewer columns then match nothing and resolve to "no vote,
        not favourited", exactly like asking per answer used to.
        """
        ids = list(answer_ids)
        if not ids:
            return {}

        answers = (
            select(Answer.id.label("answer_id")).where(Answer.id.in_(ids)).subquery()
        )
        votes = (
            select(
                Attitude.attitudable_id.label("answer_id"),
                func.count()
                .filter(Attitude.attitude == VoteType.POSITIVE.value)
                .label("positive_count"),
                func.count()
                .filter(Attitude.attitude == VoteType.NEGATIVE.value)
                .label("negative_count"),
                func.max(
                    case((Attitude.user_id == viewer_id, Attitude.attitude))
                ).label("viewer_vote"),
            )
            .where(
                Attitude.attitudable_id.in_(ids),
                Attitude.attitudable_type == "ANSWER",
            )
            .group_by(Attitude.attitudable_id)
            .subquery()
        )
        favorites = (
            select(
                AnswerFavorite.answer_id.label("answer_id"),
                func.count().label("favorite_count"),
                func.max(case((AnswerFavorite.user_id == viewer_id, 1), else_=0)).label(
                    "viewer_favorited"
                ),
            )
            .where(AnswerFavorite.answer_id.in_(ids))
            .group_by(AnswerFavorite.answer_id)
            .subquery()
        )
        comments = (
            select(
                Discussion.model_id.label("answer_id"),
                func.count().label("comment_count"),
            )
            .where(
                Discussion.model_type == DiscussableModelType.ANSWER.value,
                Discussion.model_id.in_(ids),
                Discussion.deleted_at.is_(None),
            )
            .group_by(Discussion.model_id)
            .subquery()
        )
        views = (
            select(
                AnswerQueryLog.answer_id.label("answer_id"),
                func.count().label("view_count"),
            )
            .where(AnswerQueryLog.answer_id.in_(ids))
            .group_by(AnswerQueryLog.answer_id)
            .subquery()
        )

        stmt = (
            select(
                answers.c.answer_id,
                func.coalesce(votes.c.positive_count, 0).label("positive_count"),
                func.coalesce(votes.c.negative_count, 0).label("negative_count"),
                votes.c.viewer_vote.label("user_vote"),
                func.coalesce(favorites.c.favorite_count, 0).label("favorite_count"),
                func.coalesce(favorites.c.viewer_favorited, 0).label("is_favorite"),
                func.coalesce(comments.c.comment_count, 0).label("comment_count"),
                func.coalesce(views.c.view_count, 0).label("view_count"),
            )
            .select_from(answers)
            .outerjoin(votes, votes.c.answer_id == answers.c.answer_id)
            .outerjoin(favorites, favorites.c.answer_id == answers.c.answer_id)
            .outerjoin(comments, comments.c.answer_id == answers.c.answer_id)
            .outerjoin(views, views.c.answer_id == answers.c.answer_id)
        )

        result = await self._session.execute(stmt)
        stats: dict[int, AnswerStats] = {}
        for row in result.all():
            user_vote = row.user_vote
            stats[int(row.answer_id)] = {
                "positive_count": int(row.positive_count or 0),
                "negative_count": int(row.negative_count or 0),
                "user_vote": str(user_vote) if user_vote is not None else None,
                "favorite_count": int(row.favorite_count or 0),
                "is_favorite": bool(row.is_favorite),
                "comment_count": int(row.comment_count or 0),
                "view_count": int(row.view_count or 0),
            }
        return stats

    async def list_favorites_by_user(
        self, *, user_id: int, limit: int, offset: int
    ) -> tuple[Sequence[Answer], int]:
        stmt = (
            select(Answer)
            .join(AnswerFavorite, Answer.id == AnswerFavorite.answer_id)
            .where(
                AnswerFavorite.user_id == user_id,
                Answer.deleted_at.is_(None),
            )
            .order_by(Answer.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self._session.execute(stmt)
        rows = list(result.scalars().all())

        count_stmt = (
            select(func.count())
            .select_from(Answer)
            .join(AnswerFavorite, Answer.id == AnswerFavorite.answer_id)
            .where(
                AnswerFavorite.user_id == user_id,
                Answer.deleted_at.is_(None),
            )
        )
        count_result = await self._session.execute(count_stmt)
        total = int(count_result.scalar_one() or 0)
        return rows, total

    async def has_user_answered_question(self, question_id: int, user_id: int) -> bool:
        stmt = select(func.count(Answer.id)).where(
            Answer.question_id == question_id,
            Answer.created_by_id == user_id,
            Answer.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return int(result.scalar_one() or 0) > 0

    async def get_answerer_user_ids(
        self, question_id: int, user_ids: set[int]
    ) -> set[int]:
        if not user_ids:
            return set()
        stmt = (
            select(Answer.created_by_id)
            .where(
                Answer.question_id == question_id,
                Answer.created_by_id.in_(list(user_ids)),
                Answer.deleted_at.is_(None),
            )
            .distinct()
        )
        result = await self._session.execute(stmt)
        return {r[0] for r in result.all()}

    async def list_all_answer_ids_by_user(self, user_id: int) -> list[int]:
        stmt = (
            select(Answer.id)
            .where(
                Answer.created_by_id == user_id,
                Answer.deleted_at.is_(None),
            )
            .order_by(Answer.id.asc())
        )
        result = await self._session.execute(stmt)
        return [r[0] for r in result.all()]

    async def list_by_user(
        self, *, user_id: int, limit: int, cursor: int | None = None
    ) -> tuple[Sequence[Answer], int]:
        stmt = select(Answer).where(
            Answer.created_by_id == user_id,
            Answer.deleted_at.is_(None),
        )
        if cursor is not None:
            stmt = stmt.where(Answer.id >= cursor)
        stmt = stmt.order_by(Answer.id.asc()).limit(limit)
        result = await self._session.execute(stmt)
        rows = list(result.scalars().all())

        count_stmt = select(func.count(Answer.id)).where(
            Answer.created_by_id == user_id,
            Answer.deleted_at.is_(None),
        )
        count_result = await self._session.execute(count_stmt)
        total = int(count_result.scalar_one() or 0)
        return rows, total
