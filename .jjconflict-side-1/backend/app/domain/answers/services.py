from app.core.errors import BadRequestError, ForbiddenError, NotFoundError
from app.domain.answers.models import Answer
from app.domain.answers.repositories import AnswerRepository
from app.domain.questions.models import VoteType
from app.domain.questions.repositories import QuestionRepository
from app.domain.user.repositories import UserProfileRepository


def _answer_to_dto(answer: Answer, author: dict | None = None) -> dict:
    created_at_ms = (
        int(answer.created_at.timestamp() * 1000) if answer.created_at else 0
    )
    updated_at_ms = (
        int(answer.updated_at.timestamp() * 1000) if answer.updated_at else 0
    )
    return {
        "id": answer.id,
        "question_id": answer.question_id,
        "content": answer.content,
        "created_by": answer.created_by_id,
        "author": author,
        "created_at": created_at_ms,
        "updated_at": updated_at_ms,
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
        viewer_id: int | None = None,
    ) -> tuple[list[dict], dict]:
        await self._ensure_question_exists(question_id)
        all_ids = await self._repo.list_all_answer_ids_for_question(question_id)

        if page_start is not None:
            try:
                start_idx = all_ids.index(page_start)
            except ValueError:
                start_idx = 0
        else:
            start_idx = 0

        end_idx = start_idx + page_size
        page_ids = all_ids[start_idx:end_idx]

        rows = await self._repo.list_answers_for_question(
            question_id=question_id,
            limit=page_size,
            cursor_id=page_start if page_start else (all_ids[0] if all_ids else None),
        )
        profiles = await self._profile_repo.get_profiles_by_user_ids(
            list({row.created_by_id for row in rows})
        )
        items: list[dict] = []
        for row in rows:
            dto = _answer_to_dto(
                row, author=_profile_to_dto(profiles.get(row.created_by_id))
            )
            await self._attach_answer_stats(dto, answer_id=row.id, viewer_id=viewer_id)
            items.append(dto)

        returned = len(items)
        has_prev = start_idx > 0
        prev_start = all_ids[0] if has_prev and len(all_ids) > 0 else 0
        has_more = end_idx < len(all_ids)
        next_start = all_ids[end_idx] if has_more else 0

        first_id = page_ids[0] if page_ids else 0
        # Frontend Page type (cheese-frontend/src/types/commons.ts) uses
        # camelCase. Snake-case keys here used to break pagination silently.
        page = {
            "pageStart": first_id,
            "pageSize": returned,
            "hasPrev": has_prev,
            "prevStart": prev_start,
            "hasMore": has_more,
            "nextStart": next_start,
        }
        return items, page

    async def _attach_answer_stats(
        self, dto: dict, *, answer_id: int, viewer_id: int | None
    ) -> None:
        """Add the user-facing stats the frontend Answer type requires.

        Comment/view counts are placeholders (no DB column yet); attitudes and
        favorite state are real. Keeping this in one place so list and detail
        always agree.
        """
        counts = await self._repo.count_votes(answer_id)
        positive_count = counts.get(VoteType.POSITIVE.value, 0)
        negative_count = counts.get(VoteType.NEGATIVE.value, 0)
        user_attitude = "UNDEFINED"
        if viewer_id:
            vote = await self._repo.get_user_vote(answer_id, viewer_id)
            if vote == VoteType.POSITIVE.value:
                user_attitude = "POSITIVE"
            elif vote == VoteType.NEGATIVE.value:
                user_attitude = "NEGATIVE"
        dto["attitudes"] = {
            "positive_count": positive_count,
            "negative_count": negative_count,
            "difference": positive_count - negative_count,
            "user_attitude": user_attitude,
        }
        dto["favorite_count"] = await self._repo.count_favorites(answer_id)
        dto["is_favorite"] = (
            await self._repo.is_favorited(answer_id, viewer_id) if viewer_id else False
        )
        dto["comment_count"] = await self._repo.count_comments(answer_id)
        dto["view_count"] = await self._repo.count_views(answer_id)
        dto["is_group"] = False

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
        if await self._repo.has_user_answered_question(question_id, user_id):
            raise BadRequestError("You have already answered this question")
        answer = await self._repo.create_answer(
            question_id=question_id,
            created_by_id=user_id,
            content=content,
        )
        profile = await self._profile_repo.get_profile_by_user_id(user_id)
        return _answer_to_dto(answer, author=_profile_to_dto(profile))

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
        if vote_type not in (VoteType.POSITIVE.value, VoteType.NEGATIVE.value):
            raise BadRequestError("Invalid vote type", data={"vote_type": vote_type})
        await self._repo.vote(answer_id=answer_id, user_id=user_id, vote_type=vote_type)
        counts = await self._repo.count_votes(answer_id)
        return {
            "upvotes": counts.get(VoteType.POSITIVE.value, 0),
            "downvotes": counts.get(VoteType.NEGATIVE.value, 0),
            "userVote": vote_type,
        }

    async def remove_answer_vote(self, *, answer_id: int, user_id: int) -> dict:
        await self._ensure_answer_exists(answer_id)
        await self._repo.remove_vote(answer_id=answer_id, user_id=user_id)
        counts = await self._repo.count_votes(answer_id)
        return {
            "upvotes": counts.get(VoteType.POSITIVE.value, 0),
            "downvotes": counts.get(VoteType.NEGATIVE.value, 0),
            "userVote": None,
        }

    async def get_answer_votes(self, *, answer_id: int, user_id: int | None) -> dict:
        await self._ensure_answer_exists(answer_id)
        counts = await self._repo.count_votes(answer_id)
        user_vote = None
        if user_id:
            user_vote = await self._repo.get_user_vote(answer_id, user_id)
        return {
            "upvotes": counts.get(VoteType.POSITIVE.value, 0),
            "downvotes": counts.get(VoteType.NEGATIVE.value, 0),
            "userVote": user_vote,
        }

    async def get_answer(
        self, *, answer_id: int, user_id: int | None
    ) -> tuple[dict, dict | None]:
        answer = await self._ensure_answer_exists(answer_id)
        profile = await self._profile_repo.get_profile_by_user_id(answer.created_by_id)
        dto = _answer_to_dto(answer, author=_profile_to_dto(profile))
        await self._attach_answer_stats(dto, answer_id=answer_id, viewer_id=user_id)

        question = await self._question_repo.get_by_id(answer.question_id)
        question_dto = None
        if question:
            author_profile = await self._profile_repo.get_profile_by_user_id(
                question.created_by_id
            )
            created_at_ms = (
                int(question.created_at.timestamp() * 1000)
                if question.created_at
                else 0
            )
            updated_at_ms = (
                int(question.updated_at.timestamp() * 1000)
                if question.updated_at
                else 0
            )
            question_dto = {
                "id": question.id,
                "title": question.title,
                "content": question.content,
                "type": question.type,
                "groupId": question.group_id,
                "bounty": question.bounty,
                "acceptedAnswerId": question.accepted_answer_id,
                "createdBy": question.created_by_id,
                "author": _profile_to_dto(author_profile),
                "createdAt": created_at_ms,
                "updatedAt": updated_at_ms,
            }
        return dto, question_dto

    async def update_answer(
        self, *, answer_id: int, user_id: int, content: str
    ) -> dict:
        answer = await self._ensure_answer_exists(answer_id)
        if answer.created_by_id != user_id:
            raise ForbiddenError("Only the answer owner can update this answer")
        if not content.strip():
            raise BadRequestError("content cannot be empty")
        updated = await self._repo.update_answer(answer, content=content)
        profile = await self._profile_repo.get_profile_by_user_id(updated.created_by_id)
        return _answer_to_dto(updated, author=_profile_to_dto(profile))

    async def delete_answer(self, *, answer_id: int, user_id: int) -> None:
        answer = await self._ensure_answer_exists(answer_id)
        if answer.created_by_id != user_id:
            raise ForbiddenError("Only the answer owner can delete this answer")
        await self._repo.soft_delete(answer)

    async def add_favorite(self, *, answer_id: int, user_id: int) -> dict:
        await self._ensure_answer_exists(answer_id)
        await self._repo.add_favorite(answer_id=answer_id, user_id=user_id)
        count = await self._repo.count_favorites(answer_id)
        return {"favoriteCount": count, "isFavorited": True}

    async def remove_favorite(self, *, answer_id: int, user_id: int) -> dict:
        await self._ensure_answer_exists(answer_id)
        removed = await self._repo.remove_favorite(answer_id=answer_id, user_id=user_id)
        if not removed:
            raise BadRequestError("Answer not favorited")
        count = await self._repo.count_favorites(answer_id)
        return {"favoriteCount": count, "isFavorited": False}


def _profile_to_dto(profile) -> dict | None:
    if profile is None:
        return None
    return {
        "id": profile.user_id,
        "nickname": profile.nickname,
        "avatarId": profile.avatar_id,
        "intro": profile.intro,
    }
