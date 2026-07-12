from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.answers.models import Answer, AnswerFavorite, AnswerQueryLog
from app.domain.questions.models import Attitude, VoteType


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
        from app.domain.discussion.models import DiscussableModelType, Discussion

        stmt = select(func.count(Discussion.id)).where(
            Discussion.model_type == DiscussableModelType.ANSWER.value,
            Discussion.model_id == answer_id,
            Discussion.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return int(result.scalar_one() or 0)

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
