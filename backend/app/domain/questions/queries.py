"""`QuestionRepository` 的**读那一半**：只取行，一个字都不改。

拆之前这两半挤在 `repositories.py` 的一个类里（那个文件 661 行，`QuestionRepository`
一个类占 459 行），「怎么问」和「怎么改」交替出现，读一个方法先要分清它属于哪一半。
界限只有一条：**只 SELECT 的在这里，add / flush / delete / 改字段的留在
`repositories.py`。**

为什么是基类而不是模块函数：拿同一个 `self._session` 把语句执行掉，是这些方法与宿主
之间全部的约定，而它们一直是**按方法**被调用的。所以 `QuestionQueries` 是一个没有
`__init__` 的基类，session 由 `repositories.QuestionRepository.__init__` 放进
`self._session`，`QuestionRepository` 继承它 —— services、路由和单测一直是拿一个
`QuestionRepository(session)` 调这里的所有方法，一行都不用改。

名字在 `repositories.py` 里再导出一次：`tests/unit/test_questions_repository.py`
一直按 `from app.domain.questions.repositories import _use_fts` 取它。
"""

import re
from datetime import UTC, datetime, timedelta

from sqlalchemy import Select, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.day_windows import utc_now
from app.domain.discussion.models import DiscussableModelType, Discussion
from app.domain.questions.models import (
    Attitude,
    Question,
    QuestionFollowerRelation,
    QuestionQueryLog,
    QuestionSearchLog,
    VoteType,
)

_HAS_WORD_CHAR_RE = re.compile(r"[\w]", re.UNICODE)


def _use_fts(token: str) -> bool:
    """Return True when *token* is suitable for PostgreSQL FTS.

    Short tokens (<= 2 chars) and tokens composed entirely of emoji /
    symbols produce empty tsqueries with the ``simple`` dictionary and
    must fall back to ILIKE.
    """
    return len(token) > 2 and _HAS_WORD_CHAR_RE.search(token) is not None


class QuestionQueries:
    _session: AsyncSession

    async def get_by_id(self, question_id: int) -> Question | None:
        stmt: Select[tuple[Question]] = select(Question).where(
            Question.id == question_id,
            Question.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    def _keyword_filter(keyword: str):
        """Return a WHERE clause for keyword search.

        Short keywords or emoji-only strings fall back to ILIKE. Longer,
        word-bearing keywords use PostgreSQL FTS with
        ``to_tsvector / plainto_tsquery`` which leverages the GIN index.
        """
        stripped = keyword.strip()
        if not _use_fts(stripped):
            like = f"%{stripped}%"
            return or_(Question.title.ilike(like), Question.content.ilike(like))
        tsvector = func.to_tsvector(
            text("'simple'"),
            func.coalesce(Question.title, "")
            + " "
            + func.coalesce(Question.content, ""),
        )
        tsquery = func.plainto_tsquery(text("'simple'"), stripped)
        return tsvector.op("@@")(tsquery)

    async def search(
        self,
        *,
        keyword: str | None,
        limit: int,
        offset: int,
        sort_by: str,
        sort_order: str,
    ) -> tuple[list[Question], int]:
        stmt: Select[tuple[Question]] = select(Question).where(
            Question.deleted_at.is_(None)
        )
        if keyword:
            stmt = stmt.where(self._keyword_filter(keyword))
        order_col = (
            Question.created_at if sort_by == "createdAt" else Question.updated_at
        )
        stmt = stmt.order_by(
            order_col.desc() if sort_order == "desc" else order_col.asc()
        )
        stmt = stmt.limit(limit).offset(offset)
        result = await self._session.execute(stmt)
        rows = list(result.scalars().all())

        count_stmt = select(func.count(Question.id)).where(
            Question.deleted_at.is_(None)
        )
        if keyword:
            count_stmt = count_stmt.where(self._keyword_filter(keyword))
        count_result = await self._session.execute(count_stmt)
        total = int(count_result.scalar_one() or 0)
        return rows, total

    async def _get_follow_relation(
        self, question_id: int, user_id: int
    ) -> QuestionFollowerRelation | None:
        stmt: Select[tuple[QuestionFollowerRelation]] = select(
            QuestionFollowerRelation
        ).where(
            QuestionFollowerRelation.question_id == question_id,
            QuestionFollowerRelation.follower_id == user_id,
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_followed(
        self, *, user_id: int, limit: int, offset: int
    ) -> tuple[list[Question], int]:
        stmt = (
            select(Question)
            .join(
                QuestionFollowerRelation,
                QuestionFollowerRelation.question_id == Question.id,
            )
            .where(
                QuestionFollowerRelation.follower_id == user_id,
                QuestionFollowerRelation.deleted_at.is_(None),
                Question.deleted_at.is_(None),
            )
            .order_by(QuestionFollowerRelation.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self._session.execute(stmt)
        rows = list(result.scalars().all())

        count_stmt = select(func.count(QuestionFollowerRelation.id)).where(
            QuestionFollowerRelation.follower_id == user_id,
            QuestionFollowerRelation.deleted_at.is_(None),
        )
        count_result = await self._session.execute(count_stmt)
        total = int(count_result.scalar_one() or 0)
        return rows, total

    async def count_followers(self, question_id: int) -> int:
        stmt = select(func.count(QuestionFollowerRelation.id)).where(
            QuestionFollowerRelation.question_id == question_id,
            QuestionFollowerRelation.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return int(result.scalar_one() or 0)

    async def is_following(self, question_id: int, user_id: int) -> bool:
        stmt = select(QuestionFollowerRelation.id).where(
            QuestionFollowerRelation.question_id == question_id,
            QuestionFollowerRelation.follower_id == user_id,
            QuestionFollowerRelation.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def count_comments(self, question_id: int) -> int:

        stmt = select(func.count(Discussion.id)).where(
            Discussion.model_type == DiscussableModelType.QUESTION.value,
            Discussion.model_id == question_id,
            Discussion.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return int(result.scalar_one() or 0)

    async def count_views(self, question_id: int) -> int:
        stmt = select(func.count(QuestionQueryLog.id)).where(
            QuestionQueryLog.question_id == question_id,
        )
        result = await self._session.execute(stmt)
        return int(result.scalar_one() or 0)

    async def _get_vote(self, question_id: int, user_id: int) -> Attitude | None:
        stmt: Select[tuple[Attitude]] = select(Attitude).where(
            Attitude.attitudable_id == question_id,
            Attitude.attitudable_type == "QUESTION",
            Attitude.user_id == user_id,
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_user_vote(self, question_id: int, user_id: int) -> str | None:
        vote = await self._get_vote(question_id, user_id)
        return vote.attitude if vote else None

    async def count_votes(self, question_id: int) -> dict[str, int]:
        stmt = (
            select(Attitude.attitude, func.count(Attitude.id))
            .where(
                Attitude.attitudable_id == question_id,
                Attitude.attitudable_type == "QUESTION",
            )
            .group_by(Attitude.attitude)
        )
        result = await self._session.execute(stmt)
        counts = {VoteType.POSITIVE.value: 0, VoteType.NEGATIVE.value: 0}
        for attitude, count in result.all():
            counts[attitude] = count
        return counts

    async def get_trending_questions(
        self, *, limit: int = 10, days: int = 7
    ) -> list[Question]:
        cutoff = utc_now().replace(hour=0, minute=0, second=0, microsecond=0)

        cutoff = cutoff - timedelta(days=days)
        subq = (
            select(
                QuestionQueryLog.question_id,
                func.count(QuestionQueryLog.id).label("view_count"),
            )
            .where(QuestionQueryLog.created_at >= cutoff)
            .group_by(QuestionQueryLog.question_id)
            .subquery()
        )
        stmt = (
            select(Question)
            .join(subq, Question.id == subq.c.question_id)
            .where(Question.deleted_at.is_(None))
            .order_by(subq.c.view_count.desc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_stats(self) -> dict:
        total_questions_stmt = select(func.count(Question.id)).where(
            Question.deleted_at.is_(None)
        )
        total_result = await self._session.execute(total_questions_stmt)
        total_questions = int(total_result.scalar_one() or 0)

        answered_stmt = select(func.count(Question.id)).where(
            Question.deleted_at.is_(None),
            Question.accepted_answer_id.isnot(None),
        )
        answered_result = await self._session.execute(answered_stmt)
        answered_questions = int(answered_result.scalar_one() or 0)

        total_views_stmt = select(func.count(QuestionQueryLog.id))
        views_result = await self._session.execute(total_views_stmt)
        total_views = int(views_result.scalar_one() or 0)

        return {
            "totalQuestions": total_questions,
            "answeredQuestions": answered_questions,
            "unansweredQuestions": total_questions - answered_questions,
            "totalViews": total_views,
        }

    async def get_popular_search_terms(
        self, *, limit: int = 10, days: int = 7
    ) -> list[dict]:

        cutoff = datetime.now(UTC) - timedelta(days=days)
        stmt = (
            select(
                QuestionSearchLog.keywords,
                func.count(QuestionSearchLog.id).label("count"),
            )
            .where(QuestionSearchLog.created_at >= cutoff)
            .group_by(QuestionSearchLog.keywords)
            .order_by(func.count(QuestionSearchLog.id).desc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return [{"keyword": row[0], "count": row[1]} for row in result.all()]

    async def list_followers(
        self, *, question_id: int, limit: int, offset: int
    ) -> tuple[list[int], int]:
        stmt = (
            select(QuestionFollowerRelation.follower_id)
            .where(
                QuestionFollowerRelation.question_id == question_id,
                QuestionFollowerRelation.deleted_at.is_(None),
            )
            .order_by(QuestionFollowerRelation.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self._session.execute(stmt)
        follower_ids = [row[0] for row in result.all()]

        count_stmt = select(func.count(QuestionFollowerRelation.id)).where(
            QuestionFollowerRelation.question_id == question_id,
            QuestionFollowerRelation.deleted_at.is_(None),
        )
        count_result = await self._session.execute(count_stmt)
        total = int(count_result.scalar_one() or 0)
        return follower_ids, total

    async def list_by_user(
        self, *, user_id: int, limit: int, offset: int
    ) -> tuple[list[Question], int]:
        stmt = (
            select(Question)
            .where(
                Question.created_by_id == user_id,
                Question.deleted_at.is_(None),
            )
            .order_by(Question.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self._session.execute(stmt)
        rows = list(result.scalars().all())

        count_stmt = select(func.count(Question.id)).where(
            Question.created_by_id == user_id,
            Question.deleted_at.is_(None),
        )
        count_result = await self._session.execute(count_stmt)
        total = int(count_result.scalar_one() or 0)
        return rows, total
