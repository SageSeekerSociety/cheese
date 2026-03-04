from __future__ import annotations

from typing import Sequence

from app.core.errors import BadRequestError, ForbiddenError, NotFoundError
from app.domain.answers.repositories import AnswerRepository
from app.domain.questions.models import Question, VoteType
from app.domain.questions.models import QuestionInvitation
from app.domain.questions.repositories import (
    QuestionRepository,
    QuestionTopicRepository,
    QuestionInvitationRepository,
)
from app.domain.user.repositories import UserProfileRepository


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
        profile_repo: UserProfileRepository | None = None,
    ) -> None:
        self._repo = repo
        self._topic_repo = topic_repo
        self._answer_repo = answer_repo
        self._profile_repo = profile_repo

    async def search_questions(
        self,
        *,
        keyword: str | None,
        page_size: int,
        page_start: int | None,
        sort_by: str,
        sort_order: str,
    ) -> tuple[list[dict], dict]:
        offset = max(0, page_start) if page_start is not None else 0
        rows, total = await self._repo.search(
            keyword=keyword,
            limit=page_size,
            offset=offset,
            sort_by=sort_by,
            sort_order=sort_order,
        )
        topic_map = await self._topic_repo.list_topic_ids([row.id for row in rows])
        items = []
        for row in rows:
            dto = _question_to_dto(row, include_content=False)
            dto["topicIds"] = topic_map.get(row.id, [])
            items.append(dto)
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
        if topic_ids:
            valid_topic_ids = await self._topic_repo.validate_topic_ids(list(topic_ids))
            for tid in topic_ids:
                if tid not in valid_topic_ids:
                    raise NotFoundError("Topic not found", data={"id": tid})
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

        topic_objects = await self._topic_repo.get_topics_for_question(question_id)
        dto["topics"] = topic_objects

        if self._profile_repo:
            profile = await self._profile_repo.get_profile_by_user_id(question.created_by_id)
            if profile:
                dto["author"] = {
                    "id": profile.user_id,
                    "nickname": profile.nickname,
                    "avatar_id": profile.avatar_id,
                }
            else:
                dto["author"] = {"id": question.created_by_id}
        else:
            dto["author"] = {"id": question.created_by_id}

        follow_count = await self._repo.count_followers(question_id)
        dto["follow_count"] = follow_count

        is_follow = False
        if viewer_id:
            is_follow = await self._repo.is_following(question_id, viewer_id)
        dto["is_follow"] = is_follow

        counts = await self._repo.count_votes(question_id)
        positive_count = counts.get(VoteType.POSITIVE.value, 0)
        negative_count = counts.get(VoteType.NEGATIVE.value, 0)
        user_attitude = "UNDEFINED"
        if viewer_id:
            user_vote = await self._repo.get_user_vote(question_id, viewer_id)
            user_attitude = user_vote if user_vote else "UNDEFINED"
        dto["attitudes"] = {
            "positive_count": positive_count,
            "negative_count": negative_count,
            "difference": positive_count - negative_count,
            "user_attitude": user_attitude,
        }

        answer_count = 0
        if self._answer_repo:
            answer_count = await self._answer_repo.count_answers_for_question(question_id)
        dto["answer_count"] = answer_count

        comment_count = await self._repo.count_comments(question_id)
        dto["comment_count"] = comment_count

        if question.accepted_answer_id and self._answer_repo:
            accepted = await self._answer_repo.get_by_id(question.accepted_answer_id)
            if accepted:
                dto["accepted_answer"] = {"id": accepted.id, "content": accepted.content}
            else:
                dto["accepted_answer"] = None
        else:
            dto["accepted_answer"] = None

        dto["created_at"] = dto.pop("createdAt")
        dto["updated_at"] = dto.pop("updatedAt")

        return dto

    async def follow_question(self, *, question_id: int, user_id: int) -> bool:
        await self._ensure_question_exists(question_id)
        return await self._repo.follow_question(question_id=question_id, user_id=user_id)

    async def unfollow_question(self, *, question_id: int, user_id: int) -> bool:
        await self._ensure_question_exists(question_id)
        return await self._repo.unfollow_question(question_id=question_id, user_id=user_id)

    async def list_followed(
        self, *, user_id: int, page_size: int, page_start: int | None
    ) -> tuple[list[dict], dict]:
        rows, total = await self._repo.list_followed(
            user_id=user_id, limit=page_size, offset=page_start or 0
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

    async def vote_question(self, *, question_id: int, user_id: int, vote_type: str) -> dict:
        await self._ensure_question_exists(question_id)
        if vote_type not in (VoteType.POSITIVE.value, VoteType.NEGATIVE.value):
            raise BadRequestError("Invalid vote type", data={"vote_type": vote_type})
        await self._repo.vote(question_id=question_id, user_id=user_id, vote_type=vote_type)
        counts = await self._repo.count_votes(question_id)
        return {
            "upvotes": counts.get(VoteType.POSITIVE.value, 0),
            "downvotes": counts.get(VoteType.NEGATIVE.value, 0),
            "userVote": vote_type,
        }

    async def remove_question_vote(self, *, question_id: int, user_id: int) -> dict:
        await self._ensure_question_exists(question_id)
        await self._repo.remove_vote(question_id=question_id, user_id=user_id)
        counts = await self._repo.count_votes(question_id)
        return {
            "upvotes": counts.get(VoteType.POSITIVE.value, 0),
            "downvotes": counts.get(VoteType.NEGATIVE.value, 0),
            "userVote": None,
        }

    async def get_question_votes(self, *, question_id: int, user_id: int | None) -> dict:
        await self._ensure_question_exists(question_id)
        counts = await self._repo.count_votes(question_id)
        user_vote = None
        if user_id:
            user_vote = await self._repo.get_user_vote(question_id, user_id)
        return {
            "upvotes": counts.get(VoteType.POSITIVE.value, 0),
            "downvotes": counts.get(VoteType.NEGATIVE.value, 0),
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

    async def update_question(
        self,
        *,
        question_id: int,
        user_id: int,
        title: str | None = None,
        content: str | None = None,
        type_: int | None = None,
        topic_ids: Sequence[int] | None = None,
    ) -> dict:
        question = await self._repo.get_by_id(question_id)
        if question is None:
            raise NotFoundError("Question not found", data={"id": question_id})
        if question.created_by_id != user_id:
            raise ForbiddenError("Only the question owner can update this question")
        updated = await self._repo.update_question(
            question,
            title=title,
            content=content,
            type_=type_,
        )
        if topic_ids is not None:
            await self._topic_repo.replace_topics(
                question_id=question_id,
                topic_ids=topic_ids,
                user_id=user_id,
            )
        dto = _question_to_dto(updated)
        topics = await self._topic_repo.list_topic_ids([question_id])
        dto["topicIds"] = topics.get(question_id, [])
        return dto

    async def delete_question(self, *, question_id: int, user_id: int) -> None:
        question = await self._repo.get_by_id(question_id)
        if question is None:
            raise NotFoundError("Question not found", data={"id": question_id})
        if question.created_by_id != user_id:
            raise ForbiddenError("Only the question owner can delete this question")
        await self._repo.soft_delete(question)

    async def set_bounty(self, *, question_id: int, user_id: int, bounty: int) -> dict:
        question = await self._repo.get_by_id(question_id)
        if question is None:
            raise NotFoundError("Question not found", data={"id": question_id})
        if question.created_by_id != user_id:
            raise ForbiddenError("Only the question owner can set bounty")
        if bounty < 0 or bounty > 20:
            raise BadRequestError("Bounty must be between 0 and 20")
        current_bounty = question.bounty or 0
        if bounty <= current_bounty:
            raise BadRequestError("New bounty must be higher than current bounty")
        updated = await self._repo.set_bounty(question, bounty)
        dto = _question_to_dto(updated)
        topics = await self._topic_repo.list_topic_ids([question_id])
        dto["topicIds"] = topics.get(question_id, [])
        return dto

    async def list_followers(
        self, *, question_id: int, page_size: int, page_start: int | None
    ) -> tuple[list[int], dict]:
        await self._ensure_question_exists(question_id)
        offset = page_start or 0
        follower_ids, total = await self._repo.list_followers(
            question_id=question_id, limit=page_size, offset=offset
        )
        returned = len(follower_ids)
        has_more = offset + returned < total
        next_start = offset + returned if has_more and returned > 0 else None
        page = {
            "pageStart": offset,
            "pageSize": returned,
            "hasMore": has_more,
            "nextStart": next_start,
            "total": total,
        }
        return follower_ids, page


def _invitation_to_dto(invitation: QuestionInvitation, user: dict | None = None) -> dict:
    created_at_ms = int(invitation.created_at.timestamp() * 1000) if invitation.created_at else 0
    updated_at_ms = int(invitation.updated_at.timestamp() * 1000) if invitation.updated_at else 0
    return {
        "id": invitation.id,
        "question_id": invitation.question_id,
        "user_id": invitation.user_id,
        "user": user,
        "created_at": created_at_ms,
        "updated_at": updated_at_ms,
        "is_answered": False,
    }


class QuestionInvitationService:
    def __init__(
        self,
        repo: QuestionInvitationRepository,
        question_repo: QuestionRepository,
        profile_repo: UserProfileRepository,
        answer_repo: AnswerRepository | None = None,
    ) -> None:
        self._repo = repo
        self._question_repo = question_repo
        self._profile_repo = profile_repo
        self._answer_repo = answer_repo

    async def list_invitations(
        self, *, question_id: int, page_start: int | None, page_size: int
    ) -> tuple[list[dict], dict]:
        await self._ensure_question_exists(question_id)
        offset = page_start or 0
        rows, total = await self._repo.list_invitations(
            question_id=question_id, limit=page_size, offset=offset
        )
        user_ids = {row.user_id for row in rows}
        profiles = await self._profile_repo.get_profiles_by_user_ids(user_ids)
        answered_map = await self._get_answered_map(question_id, user_ids)
        items = []
        for row in rows:
            profile = profiles.get(row.user_id)
            user_dto = _profile_to_user(profile) if profile else None
            dto = _invitation_to_dto(row, user=user_dto)
            dto["is_answered"] = answered_map.get(row.user_id, False)
            items.append(dto)
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

    async def create_invitation(
        self, *, question_id: int, inviter_id: int, invitee_id: int
    ) -> dict:
        question = await self._question_repo.get_by_id(question_id)
        if question is None:
            raise NotFoundError("Question not found", data={"id": question_id})
        if inviter_id == invitee_id:
            raise BadRequestError("Cannot invite yourself")
        profile = await self._profile_repo.get_profile_by_user_id(invitee_id)
        if profile is None:
            raise NotFoundError("User not found", data={"id": invitee_id})
        existing = await self._repo._get_invitation(question_id, invitee_id)
        if existing is not None:
            raise BadRequestError("User already invited")
        invitation = await self._repo.create_invitation(
            question_id=question_id,
            user_id=invitee_id,
        )
        user_dto = _profile_to_user(profile)
        return {
            "invitation_id": invitation.id,
            "invitation": _invitation_to_dto(invitation, user=user_dto),
        }

    async def get_invitation(self, *, invitation_id: int) -> dict:
        invitation = await self._repo.get_by_id(invitation_id)
        if invitation is None:
            raise NotFoundError("Invitation not found", data={"id": invitation_id})
        profile = await self._profile_repo.get_profile_by_user_id(invitation.user_id)
        user_dto = _profile_to_user(profile) if profile else None
        dto = _invitation_to_dto(invitation, user=user_dto)
        if self._answer_repo:
            answers = await self._answer_repo.list_answers_for_question(
                question_id=invitation.question_id, limit=1000, offset=0
            )
            for answer in answers:
                if answer.created_by_id == invitation.user_id:
                    dto["is_answered"] = True
                    break
        return dto

    async def delete_invitation(self, *, invitation_id: int, user_id: int) -> None:
        invitation = await self._repo.get_by_id(invitation_id)
        if invitation is None:
            raise BadRequestError("Invitation not found")
        question = await self._question_repo.get_by_id(invitation.question_id)
        if question is None or question.created_by_id != user_id:
            raise ForbiddenError("Only the question owner can delete invitations")
        await self._repo.hard_delete(invitation)

    async def get_recommendations(self, *, question_id: int, limit: int) -> list[dict]:
        await self._ensure_question_exists(question_id)
        all_profiles = await self._profile_repo.list_profiles(limit=limit, offset=0)
        result = []
        for profile in all_profiles:
            result.append(_profile_to_user(profile))
        return result

    async def _ensure_question_exists(self, question_id: int) -> None:
        question = await self._question_repo.get_by_id(question_id)
        if question is None:
            raise NotFoundError("Question not found", data={"id": question_id})

    async def _get_answered_map(self, question_id: int, user_ids: set[int]) -> dict[int, bool]:
        if not self._answer_repo or not user_ids:
            return {}
        answers = await self._answer_repo.list_answers_for_question(
            question_id=question_id, limit=1000, offset=0
        )
        answered = {answer.created_by_id for answer in answers}
        return {uid: uid in answered for uid in user_ids}


def _profile_to_user(profile) -> dict | None:
    if profile is None:
        return None
    return {
        "id": profile.user_id,
        "nickname": profile.nickname,
        "avatarId": profile.avatar_id,
        "intro": profile.intro,
    }
