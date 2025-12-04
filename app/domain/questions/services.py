from __future__ import annotations

from typing import Sequence

from app.core.errors import BadRequestError, ForbiddenError, NotFoundError
from app.domain.answers.repositories import AnswerRepository
from app.domain.questions.models import Question, VoteType
from app.domain.questions.repositories import QuestionRepository, QuestionTopicRepository


def _question_to_dto(question: Question, *, include_content: bool = True) -> dict:
    created_at_ms = int(question.created_at.timestamp() * 1000) if question.created_at else 0
    updated_at_ms = int(question.updated_at.timestamp() * 1000) if question.updated_at else 0
    return {
        "id": question.id,
        "title": question.title,
        "content": question.content if include_content else None,
        "type": question.type,
        "groupId": question.group_id,
        "bounty": question.bounty,
        "acceptedAnswerId": question.accepted_answer_id,
        "createdBy": question.created_by_id,
        "createdAt": created_at_ms,
        "updatedAt": updated_at_ms,
    }


class QuestionsService:
    def __init__(
        self,
        repo: QuestionRepository,
        topic_repo: QuestionTopicRepository,
        answer_repo: AnswerRepository | None = None,
    ) -> None:
        self._repo = repo
        self._topic_repo = topic_repo
        self._answer_repo = answer_repo

    async def search_questions(
        self,
        *,
        keyword: str | None,
        page_size: int,
        page_start: int | None,
        sort_by: str,
        sort_order: str,
    ) -> tuple[list[dict], dict]:
        rows, total = await self._repo.search(
            keyword=keyword,
            limit=page_size,
            offset=page_start or 0,
            sort_by=sort_by,
            sort_order=sort_order,
        )
        topic_map = await self._topic_repo.list_topic_ids([row.id for row in rows])
        items = []
        for row in rows:
            dto = _question_to_dto(row, include_content=False)
            dto["topicIds"] = topic_map.get(row.id, [])
            items.append(dto)
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

    async def create_question(
        self,
        *,
        user_id: int,
        title: str,
        content: str,
        type_: int,
        group_id: int | None,
        bounty: int,
        topic_ids: Sequence[int],
    ) -> dict:
        if not title.strip():
            raise BadRequestError("title cannot be empty")
        if not content.strip():
            raise BadRequestError("content cannot be empty")
        question = await self._repo.create_question(
            created_by_id=user_id,
            title=title.strip(),
            content=content,
            type_=type_,
            group_id=group_id,
            bounty=bounty,
        )
        if topic_ids:
            await self._topic_repo.replace_topics(
                question_id=question.id,
                topic_ids=topic_ids,
                user_id=user_id,
            )
        dto = _question_to_dto(question)
        dto["topicIds"] = topic_ids
        return dto

    async def get_question(self, question_id: int, viewer_id: int | None) -> dict:
        question = await self._repo.get_by_id(question_id)
        if question is None:
            raise NotFoundError("Question not found", data={"id": question_id})
        dto = _question_to_dto(question)
        topics = await self._topic_repo.list_topic_ids([question_id])
        dto["topicIds"] = topics.get(question_id, [])
        dto["followers"] = await self._repo.count_followers(question_id)
        return dto

    async def follow_question(self, *, question_id: int, user_id: int) -> bool:
        await self._ensure_question_exists(question_id)
        return await self._repo.follow_question(question_id=question_id, user_id=user_id)

    async def unfollow_question(self, *, question_id: int, user_id: int) -> bool:
        await self._ensure_question_exists(question_id)
        return await self._repo.unfollow_question(question_id=question_id, user_id=user_id)

    async def list_followed(self, *, user_id: int, page_size: int, page_start: int | None) -> tuple[list[dict], dict]:
        rows, total = await self._repo.list_followed(user_id=user_id, limit=page_size, offset=page_start or 0)
        topic_map = await self._topic_repo.list_topic_ids([row.id for row in rows])
        items = []
        for row in rows:
            dto = _question_to_dto(row, include_content=False)
            dto["topicIds"] = topic_map.get(row.id, [])
            items.append(dto)
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

    async def _ensure_question_exists(self, question_id: int) -> None:
        exists = await self._repo.get_by_id(question_id)
        if exists is None:
            raise NotFoundError("Question not found", data={"id": question_id})

    async def accept_answer(self, *, question_id: int, answer_id: int, user_id: int) -> dict:
        question = await self._repo.get_by_id(question_id)
        if question is None:
            raise NotFoundError("Question not found", data={"id": question_id})
        if question.created_by_id != user_id:
            raise ForbiddenError("Only the question owner can accept an answer")
        if self._answer_repo is None:
            raise BadRequestError("Answer repository not configured")
        answer = await self._answer_repo.get_by_id(answer_id)
        if answer is None:
            raise NotFoundError("Answer not found", data={"id": answer_id})
        if answer.question_id != question_id:
            raise BadRequestError("Answer does not belong to this question")
        updated = await self._repo.accept_answer(question_id=question_id, answer_id=answer_id)
        if updated is None:
            raise NotFoundError("Question not found", data={"id": question_id})
        dto = _question_to_dto(updated)
        topics = await self._topic_repo.list_topic_ids([question_id])
        dto["topicIds"] = topics.get(question_id, [])
        return dto

    async def unaccept_answer(self, *, question_id: int, user_id: int) -> dict:
        question = await self._repo.get_by_id(question_id)
        if question is None:
            raise NotFoundError("Question not found", data={"id": question_id})
        if question.created_by_id != user_id:
            raise ForbiddenError("Only the question owner can unaccept an answer")
        updated = await self._repo.unaccept_answer(question_id=question_id)
        if updated is None:
            raise NotFoundError("Question not found", data={"id": question_id})
        dto = _question_to_dto(updated)
        topics = await self._topic_repo.list_topic_ids([question_id])
        dto["topicIds"] = topics.get(question_id, [])
        return dto

    async def vote_question(
        self, *, question_id: int, user_id: int, vote_type: str
    ) -> dict:
        await self._ensure_question_exists(question_id)
        if vote_type not in (VoteType.UPVOTE.value, VoteType.DOWNVOTE.value):
            raise BadRequestError("Invalid vote type", data={"vote_type": vote_type})
        await self._repo.vote(question_id=question_id, user_id=user_id, vote_type=vote_type)
        counts = await self._repo.count_votes(question_id)
        return {
            "upvotes": counts.get(VoteType.UPVOTE.value, 0),
            "downvotes": counts.get(VoteType.DOWNVOTE.value, 0),
            "userVote": vote_type,
        }

    async def remove_question_vote(self, *, question_id: int, user_id: int) -> dict:
        await self._ensure_question_exists(question_id)
        await self._repo.remove_vote(question_id=question_id, user_id=user_id)
        counts = await self._repo.count_votes(question_id)
        return {
            "upvotes": counts.get(VoteType.UPVOTE.value, 0),
            "downvotes": counts.get(VoteType.DOWNVOTE.value, 0),
            "userVote": None,
        }

    async def get_question_votes(self, *, question_id: int, user_id: int | None) -> dict:
        await self._ensure_question_exists(question_id)
        counts = await self._repo.count_votes(question_id)
        user_vote = None
        if user_id:
            user_vote = await self._repo.get_user_vote(question_id, user_id)
        return {
            "upvotes": counts.get(VoteType.UPVOTE.value, 0),
            "downvotes": counts.get(VoteType.DOWNVOTE.value, 0),
            "userVote": user_vote,
        }

    async def get_trending_questions(self, *, limit: int = 10, days: int = 7) -> list[dict]:
        questions = await self._repo.get_trending_questions(limit=limit, days=days)
        topic_map = await self._topic_repo.list_topic_ids([q.id for q in questions])
        items = []
        for q in questions:
            dto = _question_to_dto(q, include_content=False)
            dto["topicIds"] = topic_map.get(q.id, [])
            items.append(dto)
        return items

    async def get_stats(self) -> dict:
        return await self._repo.get_stats()

    async def get_popular_search_terms(self, *, limit: int = 10, days: int = 7) -> list[dict]:
        return await self._repo.get_popular_search_terms(limit=limit, days=days)
