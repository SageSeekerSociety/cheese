from __future__ import annotations

from typing import Sequence

from app.core.errors import BadRequestError, NotFoundError
from app.domain.answers.models import Answer
from app.domain.answers.repositories import AnswerRepository
from app.domain.questions.models import VoteType
from app.domain.questions.repositories import QuestionRepository
from app.domain.user.repositories import UserProfileRepository


def _answer_to_dto(answer: Answer, sender: dict | None = None) -> dict:
    created_at_ms = int(answer.created_at.timestamp() * 1000) if answer.created_at else 0
    updated_at_ms = int(answer.updated_at.timestamp() * 1000) if answer.updated_at else 0
    return {
        "id": answer.id,
        "questionId": answer.question_id,
        "content": answer.content,
        "createdBy": answer.created_by_id,
        "sender": sender,
        "createdAt": created_at_ms,
        "updatedAt": updated_at_ms,
    }


class AnswersService:
    def __init__(
        self,
        repo: AnswerRepository,
        question_repo: QuestionRepository,
        profile_repo: UserProfileRepository,
    ) -> None:
        self._repo = repo
        self._question_repo = question_repo
        self._profile_repo = profile_repo

    async def list_answers(
        self,
        *,
        question_id: int,
        page_start: int | None,
        page_size: int,
    ) -> tuple[list[dict], dict]:
        await self._ensure_question_exists(question_id)
        rows = await self._repo.list_answers_for_question(
            question_id=question_id,
            limit=page_size,
            offset=page_start or 0,
        )
        profiles = await self._profile_repo.get_profiles_by_user_ids({row.created_by_id for row in rows})
        items = [
            _answer_to_dto(row, sender=_profile_to_dto(profiles.get(row.created_by_id)))
            for row in rows
        ]
        total = await self._repo.count_answers_for_question(question_id)
        offset = page_start or 0
        returned = len(items)
        has_more = offset + returned < total
        next_start = offset + returned if has_more and returned > 0 else None
        page = {
            "pageStart": offset,
            "pageSize": returned,
            "hasMore": has_more,
            "nextStart": next_start,
            "total": total,
        }
        return items, page

    async def create_answer(
        self,
        *,
        question_id: int,
        user_id: int,
        content: str,
    ) -> dict:
        await self._ensure_question_exists(question_id)
        if not content.strip():
            raise BadRequestError("content cannot be empty")
        answer = await self._repo.create_answer(
            question_id=question_id,
            created_by_id=user_id,
            content=content,
        )
        profile = await self._profile_repo.get_profile_by_user_id(user_id)
        return _answer_to_dto(answer, sender=_profile_to_dto(profile))

    async def _ensure_question_exists(self, question_id: int) -> None:
        question = await self._question_repo.get_by_id(question_id)
        if question is None:
            raise NotFoundError("Question not found", data={"id": question_id})

    async def _ensure_answer_exists(self, answer_id: int) -> Answer:
        answer = await self._repo.get_by_id(answer_id)
        if answer is None:
            raise NotFoundError("Answer not found", data={"id": answer_id})
        return answer

    async def vote_answer(
        self, *, answer_id: int, user_id: int, vote_type: str
    ) -> dict:
        await self._ensure_answer_exists(answer_id)
        if vote_type not in (VoteType.UPVOTE.value, VoteType.DOWNVOTE.value):
            raise BadRequestError("Invalid vote type", data={"vote_type": vote_type})
        await self._repo.vote(answer_id=answer_id, user_id=user_id, vote_type=vote_type)
        counts = await self._repo.count_votes(answer_id)
        return {
            "upvotes": counts.get(VoteType.UPVOTE.value, 0),
            "downvotes": counts.get(VoteType.DOWNVOTE.value, 0),
            "userVote": vote_type,
        }

    async def remove_answer_vote(self, *, answer_id: int, user_id: int) -> dict:
        await self._ensure_answer_exists(answer_id)
        await self._repo.remove_vote(answer_id=answer_id, user_id=user_id)
        counts = await self._repo.count_votes(answer_id)
        return {
            "upvotes": counts.get(VoteType.UPVOTE.value, 0),
            "downvotes": counts.get(VoteType.DOWNVOTE.value, 0),
            "userVote": None,
        }

    async def get_answer_votes(self, *, answer_id: int, user_id: int | None) -> dict:
        await self._ensure_answer_exists(answer_id)
        counts = await self._repo.count_votes(answer_id)
        user_vote = None
        if user_id:
            user_vote = await self._repo.get_user_vote(answer_id, user_id)
        return {
            "upvotes": counts.get(VoteType.UPVOTE.value, 0),
            "downvotes": counts.get(VoteType.DOWNVOTE.value, 0),
            "userVote": user_vote,
        }


def _profile_to_dto(profile) -> dict | None:
    if profile is None:
        return None
    return {
        "id": profile.user_id,
        "nickname": profile.nickname,
        "avatarId": profile.avatar_id,
        "intro": profile.intro,
    }
