import re
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import Select, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.questions.models import (
    Attitude,
    Question,
    QuestionFollowerRelation,
    QuestionInvitation,
    QuestionQueryLog,
    QuestionSearchLog,
    QuestionTopicRelation,
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


class QuestionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_question(
        self,
        *,
        created_by_id: int,
        title: str,
        content: str,
        type_: int,
        group_id: int | None,
        bounty: int,
    ) -> Question:
        now = datetime.now(UTC)
        question = Question(
            created_by_id=created_by_id,
            title=title,
            content=content,
            type=type_,
            group_id=group_id,
            bounty=bounty,
            created_at=now,
            updated_at=now,
            deleted_at=None,
        )
        self._session.add(question)
        await self._session.flush()
        return question

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
            func.coalesce(Question.title, "") + " " + func.coalesce(Question.content, ""),
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
        stmt: Select[tuple[Question]] = select(Question).where(Question.deleted_at.is_(None))
        if keyword:
            stmt = stmt.where(self._keyword_filter(keyword))
        order_col = Question.created_at if sort_by == "createdAt" else Question.updated_at
        stmt = stmt.order_by(order_col.desc() if sort_order == "desc" else order_col.asc())
        stmt = stmt.limit(limit).offset(offset)
        result = await self._session.execute(stmt)
        rows = list(result.scalars().all())

        count_stmt = select(func.count(Question.id)).where(Question.deleted_at.is_(None))
        if keyword:
            count_stmt = count_stmt.where(self._keyword_filter(keyword))
        count_result = await self._session.execute(count_stmt)
        total = int(count_result.scalar_one() or 0)
        return rows, total

    async def follow_question(self, *, question_id: int, user_id: int) -> bool:
        existing = await self._get_follow_relation(question_id, user_id)
        if existing is not None and existing.deleted_at is None:
            return False
        now = datetime.now(UTC)
        if existing is None:
            relation = QuestionFollowerRelation(
                question_id=question_id,
                follower_id=user_id,
                created_at=now,
                deleted_at=None,
            )
            self._session.add(relation)
        else:
            existing.deleted_at = None
            existing.created_at = now
        await self._session.flush()
        return True

    async def unfollow_question(self, *, question_id: int, user_id: int) -> bool:
        relation = await self._get_follow_relation(question_id, user_id)
        if relation is None or relation.deleted_at is not None:
            return False
        relation.deleted_at = datetime.now(UTC)
        await self._session.flush()
        return True

    async def _get_follow_relation(
        self, question_id: int, user_id: int
    ) -> QuestionFollowerRelation | None:
        stmt: Select[tuple[QuestionFollowerRelation]] = select(QuestionFollowerRelation).where(
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
            .join(QuestionFollowerRelation, QuestionFollowerRelation.question_id == Question.id)
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
        from app.domain.discussion.models import DiscussableModelType, Discussion

        stmt = select(func.count(Discussion.id)).where(
            Discussion.model_type == DiscussableModelType.QUESTION.value,
            Discussion.model_id == question_id,
            Discussion.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return int(result.scalar_one() or 0)

    async def accept_answer(self, *, question_id: int, answer_id: int) -> Question | None:
        """Set accepted_answer_id for a question."""
        question = await self.get_by_id(question_id)
        if question is None:
            return None
        question.accepted_answer_id = answer_id
        question.updated_at = datetime.now(UTC)
        await self._session.flush()
        return question

    async def unaccept_answer(self, *, question_id: int) -> Question | None:
        """Clear accepted_answer_id for a question."""
        question = await self.get_by_id(question_id)
        if question is None:
            return None
        question.accepted_answer_id = None
        question.updated_at = datetime.now(UTC)
        await self._session.flush()
        return question

    async def log_query(
        self,
        *,
        question_id: int,
        viewer_id: int | None,
        ip: str,
        user_agent: str | None,
    ) -> None:
        log = QuestionQueryLog(
            question_id=question_id,
            viewer_id=viewer_id,
            ip=ip,
            user_agent=user_agent,
            created_at=datetime.now(UTC),
        )
        self._session.add(log)
        await self._session.flush()

    async def count_views(self, question_id: int) -> int:
        stmt = select(func.count(QuestionQueryLog.id)).where(
            QuestionQueryLog.question_id == question_id,
        )
        result = await self._session.execute(stmt)
        return int(result.scalar_one() or 0)

    async def vote(self, *, question_id: int, user_id: int, vote_type: str) -> Attitude:
        existing = await self._get_vote(question_id, user_id)
        now = datetime.now(UTC)
        if existing is not None:
            existing.attitude = vote_type
            existing.updated_at = now
            await self._session.flush()
            return existing
        attitude = Attitude(
            attitudable_id=question_id,
            attitudable_type="QUESTION",
            user_id=user_id,
            attitude=vote_type,
            created_at=now,
            updated_at=now,
        )
        self._session.add(attitude)
        await self._session.flush()
        return attitude

    async def remove_vote(self, *, question_id: int, user_id: int) -> bool:
        existing = await self._get_vote(question_id, user_id)
        if existing is None:
            return False
        await self._session.delete(existing)
        await self._session.flush()
        return True

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

    async def log_search(
        self,
        *,
        keywords: str,
        first_question_id: int | None,
        page_size: int,
        result_count: int,
        duration_ms: float,
        searcher_id: int | None,
        ip: str,
        user_agent: str | None,
    ) -> None:
        log = QuestionSearchLog(
            keywords=keywords,
            first_question_id=first_question_id,
            page_size=page_size,
            result=str(result_count),
            duration=duration_ms,
            searcher_id=searcher_id,
            ip=ip,
            user_agent=user_agent,
            created_at=datetime.now(UTC),
        )
        self._session.add(log)
        await self._session.flush()

    async def get_trending_questions(self, *, limit: int = 10, days: int = 7) -> list[Question]:
        cutoff = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        from datetime import timedelta

        cutoff = cutoff - timedelta(days=days)
        subq = (
            select(
                QuestionQueryLog.question_id, func.count(QuestionQueryLog.id).label("view_count")
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
        total_questions_stmt = select(func.count(Question.id)).where(Question.deleted_at.is_(None))
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

    async def get_popular_search_terms(self, *, limit: int = 10, days: int = 7) -> list[dict]:
        from datetime import timedelta

        cutoff = datetime.now(UTC) - timedelta(days=days)
        stmt = (
            select(QuestionSearchLog.keywords, func.count(QuestionSearchLog.id).label("count"))
            .where(QuestionSearchLog.created_at >= cutoff)
            .group_by(QuestionSearchLog.keywords)
            .order_by(func.count(QuestionSearchLog.id).desc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return [{"keyword": row[0], "count": row[1]} for row in result.all()]

    async def update_question(
        self,
        question: Question,
        *,
        title: str | None = None,
        content: str | None = None,
        type_: int | None = None,
    ) -> Question:
        if title is not None:
            question.title = title
        if content is not None:
            question.content = content
        if type_ is not None:
            question.type = type_
        question.updated_at = datetime.now(UTC)
        await self._session.flush()
        return question

    async def soft_delete(self, question: Question) -> None:
        question.deleted_at = datetime.now(UTC)
        await self._session.flush()

    async def set_bounty(self, question: Question, bounty: int) -> Question:
        question.bounty = bounty
        question.updated_at = datetime.now(UTC)
        await self._session.flush()
        return question

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


class QuestionTopicRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def replace_topics(
        self, *, question_id: int, topic_ids: Sequence[int], user_id: int
    ) -> None:
        stmt = select(QuestionTopicRelation).where(
            QuestionTopicRelation.question_id == question_id,
            QuestionTopicRelation.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        existing = list(result.scalars().all())
        now = datetime.now(UTC)
        for row in existing:
            row.deleted_at = now
        for topic_id in topic_ids:
            self._session.add(
                QuestionTopicRelation(
                    question_id=question_id,
                    topic_id=topic_id,
                    created_by_id=user_id,
                    created_at=now,
                    deleted_at=None,
                )
            )
        await self._session.flush()

    async def list_topic_ids(self, question_ids: Sequence[int]) -> dict[int, list[int]]:
        if not question_ids:
            return {}
        stmt: Select[tuple[QuestionTopicRelation]] = (
            select(QuestionTopicRelation)
            .where(
                QuestionTopicRelation.question_id.in_(list(question_ids)),
                QuestionTopicRelation.deleted_at.is_(None),
            )
            .order_by(QuestionTopicRelation.question_id.asc(), QuestionTopicRelation.id.asc())
        )
        result = await self._session.execute(stmt)
        mapping: dict[int, list[int]] = {}
        for row in result.scalars().all():
            mapping.setdefault(row.question_id, []).append(row.topic_id)
        return mapping

    async def validate_topic_ids(self, topic_ids: list[int]) -> set[int]:
        from app.domain.topics.models import Topic

        if not topic_ids:
            return set()
        stmt = select(Topic.id).where(
            Topic.id.in_(topic_ids),
            Topic.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return set(result.scalars().all())

    async def get_topics_for_question(self, question_id: int) -> list[dict]:
        from app.domain.topics.models import Topic

        stmt = (
            select(Topic)
            .join(QuestionTopicRelation, QuestionTopicRelation.topic_id == Topic.id)
            .where(
                QuestionTopicRelation.question_id == question_id,
                QuestionTopicRelation.deleted_at.is_(None),
                Topic.deleted_at.is_(None),
            )
        )
        result = await self._session.execute(stmt)
        topics = result.scalars().all()
        return [{"id": t.id, "name": t.name} for t in topics]

    async def get_topics_for_questions(self, question_ids: Sequence[int]) -> dict[int, list[dict]]:
        """Bulk variant of get_topics_for_question. Returns {question_id: [{id, name}]}."""
        from app.domain.topics.models import Topic

        if not question_ids:
            return {}
        stmt = (
            select(QuestionTopicRelation.question_id, Topic.id, Topic.name)
            .join(Topic, Topic.id == QuestionTopicRelation.topic_id)
            .where(
                QuestionTopicRelation.question_id.in_(list(question_ids)),
                QuestionTopicRelation.deleted_at.is_(None),
                Topic.deleted_at.is_(None),
            )
            .order_by(
                QuestionTopicRelation.question_id.asc(),
                QuestionTopicRelation.id.asc(),
            )
        )
        result = await self._session.execute(stmt)
        mapping: dict[int, list[dict]] = {}
        for question_id, topic_id, topic_name in result.all():
            mapping.setdefault(question_id, []).append({"id": topic_id, "name": topic_name})
        return mapping


class QuestionInvitationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_invitation(self, *, question_id: int, user_id: int) -> QuestionInvitation:
        existing = await self._get_invitation(question_id, user_id)
        now = datetime.now(UTC)
        if existing is not None:
            existing.updated_at = now
            await self._session.flush()
            return existing
        invitation = QuestionInvitation(
            question_id=question_id,
            user_id=user_id,
            created_at=now,
            updated_at=now,
        )
        self._session.add(invitation)
        await self._session.flush()
        return invitation

    async def get_by_id(self, invitation_id: int) -> QuestionInvitation | None:
        stmt: Select[tuple[QuestionInvitation]] = select(QuestionInvitation).where(
            QuestionInvitation.id == invitation_id,
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_invitations(
        self, *, question_id: int, limit: int, offset: int
    ) -> tuple[list[QuestionInvitation], int]:
        stmt = (
            select(QuestionInvitation)
            .where(
                QuestionInvitation.question_id == question_id,
            )
            .order_by(QuestionInvitation.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self._session.execute(stmt)
        rows = list(result.scalars().all())

        count_stmt = select(func.count(QuestionInvitation.id)).where(
            QuestionInvitation.question_id == question_id,
        )
        count_result = await self._session.execute(count_stmt)
        total = int(count_result.scalar_one() or 0)
        return rows, total

    async def hard_delete(self, invitation: QuestionInvitation) -> None:
        await self._session.delete(invitation)
        await self._session.flush()

    async def _get_invitation(self, question_id: int, user_id: int) -> QuestionInvitation | None:
        stmt: Select[tuple[QuestionInvitation]] = select(QuestionInvitation).where(
            QuestionInvitation.question_id == question_id,
            QuestionInvitation.user_id == user_id,
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()
