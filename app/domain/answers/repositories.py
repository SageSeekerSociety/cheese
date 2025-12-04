from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone

from sqlalchemy import Select, and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.answers.models import Answer, AnswerVote
from app.domain.questions.models import VoteType


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
        now = datetime.now(timezone.utc)
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
        offset: int,
    ) -> Sequence[Answer]:
        stmt: Select[tuple[Answer]] = select(Answer).where(
            Answer.question_id == question_id,
            Answer.deleted_at.is_(None),
        ).order_by(Answer.created_at.desc()).limit(limit).offset(offset)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

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

    async def vote(self, *, answer_id: int, user_id: int, vote_type: str) -> AnswerVote:
        existing = await self._get_vote(answer_id, user_id)
        now = datetime.now(timezone.utc)
        if existing is not None:
            existing.vote_type = vote_type
            existing.updated_at = now
            await self._session.flush()
            return existing
        vote = AnswerVote(
            answer_id=answer_id,
            user_id=user_id,
            vote_type=vote_type,
            created_at=now,
            updated_at=now,
        )
        self._session.add(vote)
        await self._session.flush()
        return vote

    async def remove_vote(self, *, answer_id: int, user_id: int) -> bool:
        existing = await self._get_vote(answer_id, user_id)
        if existing is None:
            return False
        await self._session.delete(existing)
        await self._session.flush()
        return True

    async def _get_vote(self, answer_id: int, user_id: int) -> AnswerVote | None:
        stmt: Select[tuple[AnswerVote]] = select(AnswerVote).where(
            AnswerVote.answer_id == answer_id,
            AnswerVote.user_id == user_id,
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_user_vote(self, answer_id: int, user_id: int) -> str | None:
        vote = await self._get_vote(answer_id, user_id)
        return vote.vote_type if vote else None

    async def count_votes(self, answer_id: int) -> dict[str, int]:
        stmt = select(AnswerVote.vote_type, func.count(AnswerVote.id)).where(
            AnswerVote.answer_id == answer_id
        ).group_by(AnswerVote.vote_type)
        result = await self._session.execute(stmt)
        counts = {VoteType.UPVOTE.value: 0, VoteType.DOWNVOTE.value: 0}
        for vote_type, count in result.all():
            counts[vote_type] = count
        return counts
