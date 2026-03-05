from typing import Annotated

from fastapi import APIRouter, Body, Depends, Path, Query

from app.auth.checker import get_auth_user
from app.auth.core import AuthUserInfo
from app.core.errors import BadRequestError
from app.db.session import get_db
from app.domain.answers.repositories import AnswerRepository
from app.domain.answers.services import AnswersService
from app.domain.discussion.models import DiscussableModelType
from app.domain.discussion.reaction_services import DiscussionReactionService
from app.domain.discussion.repositories import (
    DiscussionReactionRepository,
    DiscussionRepository,
    ReactionTypeRepository,
)
from app.domain.discussion.services import DiscussionService
from app.domain.questions.repositories import QuestionRepository
from app.domain.user.repositories import UserProfileRepository


router = APIRouter(prefix="/questions/{question_id}/answers", tags=["Answers"])


async def get_answers_service(db=Depends(get_db)) -> AnswersService:
    answer_repo = AnswerRepository(session=db)
    question_repo = QuestionRepository(session=db)
    profile_repo = UserProfileRepository(session=db)
    return AnswersService(
        repo=answer_repo,
        question_repo=question_repo,
        profile_repo=profile_repo,
    )


async def get_discussion_service(db=Depends(get_db)) -> DiscussionService:
    discussion_repo = DiscussionRepository(session=db)
    reaction_repo = DiscussionReactionRepository(session=db)
    reaction_type_repo = ReactionTypeRepository(session=db)
    reaction_service = DiscussionReactionService(
        reaction_repo=reaction_repo, reaction_type_repo=reaction_type_repo
    )
    profile_repo = UserProfileRepository(session=db)
    return DiscussionService(
        repo=discussion_repo,
        reaction_service=reaction_service,
        profile_repo=profile_repo,
        session=db,
    )


@router.get(
    "",
    summary="List Answers",
)
async def list_answers(
    question_id: Annotated[int, Path(ge=0)],
    page_start: int | None = Query(default=None, alias="page_start"),
    page_size: int = Query(default=20, ge=1, le=100, alias="page_size"),
    auth_user: AuthUserInfo = Depends(get_auth_user),
    service: AnswersService = Depends(get_answers_service),
) -> dict:
    _ = auth_user
    answers, page = await service.list_answers(
        question_id=question_id,
        page_start=page_start,
        page_size=page_size,
    )
    return {"code": 200, "message": "OK", "data": {"answers": answers, "page": page}}


@router.post(
    "",
    summary="Create Answer",
    status_code=201,
)
async def create_answer(
    question_id: Annotated[int, Path(ge=0)],
    payload: dict = Body(...),
    auth_user: AuthUserInfo = Depends(get_auth_user),
    service: AnswersService = Depends(get_answers_service),
) -> dict:
    content = payload.get("content")
    if not isinstance(content, str) or not content.strip():
        raise BadRequestError("content is required")
    answer = await service.create_answer(
        question_id=question_id,
        user_id=auth_user.user_id,
        content=content,
    )
    return {
        "code": 201,
        "message": "Created",
        "data": {"id": answer["id"], "answer": answer},
    }


@router.post(
    "/{answer_id}/vote",
    summary="Vote on Answer",
)
async def vote_answer(
    question_id: Annotated[int, Path(ge=0)],
    answer_id: Annotated[int, Path(ge=0)],
    payload: dict = Body(...),
    auth_user: AuthUserInfo = Depends(get_auth_user),
    service: AnswersService = Depends(get_answers_service),
) -> dict:
    _ = question_id
    vote_type = payload.get("voteType", "UPVOTE")
    result = await service.vote_answer(
        answer_id=answer_id, user_id=auth_user.user_id, vote_type=vote_type
    )
    return {"code": 200, "message": "OK", "data": result}


@router.delete(
    "/{answer_id}/vote",
    summary="Remove Vote from Answer",
)
async def remove_answer_vote(
    question_id: Annotated[int, Path(ge=0)],
    answer_id: Annotated[int, Path(ge=0)],
    auth_user: AuthUserInfo = Depends(get_auth_user),
    service: AnswersService = Depends(get_answers_service),
) -> dict:
    _ = question_id
    result = await service.remove_answer_vote(answer_id=answer_id, user_id=auth_user.user_id)
    return {"code": 200, "message": "OK", "data": result}


@router.get(
    "/{answer_id}/vote",
    summary="Get Answer Votes",
)
async def get_answer_votes(
    question_id: Annotated[int, Path(ge=0)],
    answer_id: Annotated[int, Path(ge=0)],
    auth_user: AuthUserInfo = Depends(get_auth_user),
    service: AnswersService = Depends(get_answers_service),
) -> dict:
    _ = question_id
    user_id = auth_user.user_id if auth_user.user_id > 0 else None
    result = await service.get_answer_votes(answer_id=answer_id, user_id=user_id)
    return {"code": 200, "message": "OK", "data": result}


@router.get(
    "/{answer_id}/comments",
    summary="List Answer Comments",
)
async def list_answer_comments(
    question_id: Annotated[int, Path(ge=0)],
    answer_id: Annotated[int, Path(ge=0)],
    page_start: int | None = Query(default=None, alias="page_start"),
    page_size: int = Query(default=20, ge=1, le=100, alias="page_size"),
    sort_by: str = Query(default="createdAt"),
    sort_order: str = Query(default="asc"),
    auth_user: AuthUserInfo = Depends(get_auth_user),
    discussion_service: DiscussionService = Depends(get_discussion_service),
) -> dict:
    _ = question_id
    user_id = auth_user.user_id if auth_user.user_id > 0 else None
    items, page = await discussion_service.list_discussions(
        model_type=DiscussableModelType.ANSWER.value,
        model_id=answer_id,
        parent_id=None,
        page_start=page_start,
        page_size=page_size,
        sort_by=sort_by,
        sort_order=sort_order,
        current_user_id=user_id,
        include_subs=True,
        with_reactions=True,
    )
    return {"code": 200, "message": "OK", "data": {"comments": items, "page": page}}


@router.post(
    "/{answer_id}/comments",
    summary="Create Answer Comment",
)
async def create_answer_comment(
    question_id: Annotated[int, Path(ge=0)],
    answer_id: Annotated[int, Path(ge=0)],
    payload: dict = Body(...),
    auth_user: AuthUserInfo = Depends(get_auth_user),
    discussion_service: DiscussionService = Depends(get_discussion_service),
) -> dict:
    _ = question_id
    content = payload.get("content", "")
    parent_id = payload.get("parentId")
    mentioned_user_ids = payload.get("mentionedUserIds", [])
    comment = await discussion_service.create_discussion(
        user_id=auth_user.user_id,
        content=content,
        model_type=DiscussableModelType.ANSWER.value,
        model_id=answer_id,
        parent_id=parent_id,
        mentioned_user_ids=mentioned_user_ids,
    )
    return {"code": 201, "message": "Created", "data": {"comment": comment}}


@router.delete(
    "/{answer_id}/comments/{comment_id}",
    summary="Delete Answer Comment",
)
async def delete_answer_comment(
    question_id: Annotated[int, Path(ge=0)],
    answer_id: Annotated[int, Path(ge=0)],
    comment_id: Annotated[int, Path(ge=0)],
    auth_user: AuthUserInfo = Depends(get_auth_user),
    discussion_service: DiscussionService = Depends(get_discussion_service),
) -> dict:
    _ = question_id
    _ = answer_id
    if auth_user.user_id == 0:
        from app.core.errors import ForbiddenError

        raise ForbiddenError("Authentication required")
    discussion = await discussion_service.get_discussion(comment_id, auth_user.user_id)
    if discussion["sender"]["id"] != auth_user.user_id:
        from app.core.errors import ForbiddenError

        raise ForbiddenError("Only the author can delete this comment")
    await discussion_service.delete_discussion(comment_id)
    return {"code": 200, "message": "OK", "data": {"deleted": True}}


@router.get(
    "/{answer_id}",
    summary="Get Answer",
)
async def get_answer(
    question_id: Annotated[int, Path(ge=0)],
    answer_id: Annotated[int, Path(ge=0)],
    auth_user: AuthUserInfo = Depends(get_auth_user),
    service: AnswersService = Depends(get_answers_service),
) -> dict:
    user_id = auth_user.user_id if auth_user.user_id > 0 else None
    answer, question = await service.get_answer(answer_id=answer_id, user_id=user_id)
    if answer["question_id"] != question_id:
        from app.core.errors import NotFoundError

        raise NotFoundError("Answer not found for this question")
    return {"code": 200, "message": "OK", "data": {"answer": answer, "question": question}}


@router.put(
    "/{answer_id}",
    summary="Update Answer",
)
async def update_answer(
    question_id: Annotated[int, Path(ge=0)],
    answer_id: Annotated[int, Path(ge=0)],
    payload: dict = Body(...),
    auth_user: AuthUserInfo = Depends(get_auth_user),
    service: AnswersService = Depends(get_answers_service),
) -> dict:
    from app.core.errors import NotFoundError

    answer_data, _ = await service.get_answer(answer_id=answer_id, user_id=auth_user.user_id)
    if answer_data["question_id"] != question_id:
        raise NotFoundError("Answer not found for this question")
    content = payload.get("content")
    if not isinstance(content, str) or not content.strip():
        raise BadRequestError("content is required")
    answer = await service.update_answer(
        answer_id=answer_id,
        user_id=auth_user.user_id,
        content=content,
    )
    return {"code": 200, "message": "OK", "data": {"answer": answer}}


@router.delete(
    "/{answer_id}",
    summary="Delete Answer",
)
async def delete_answer(
    question_id: Annotated[int, Path(ge=0)],
    answer_id: Annotated[int, Path(ge=0)],
    auth_user: AuthUserInfo = Depends(get_auth_user),
    service: AnswersService = Depends(get_answers_service),
) -> dict:
    from app.core.errors import NotFoundError

    answer_data, _ = await service.get_answer(answer_id=answer_id, user_id=auth_user.user_id)
    if answer_data["question_id"] != question_id:
        raise NotFoundError("Answer not found for this question")
    await service.delete_answer(answer_id=answer_id, user_id=auth_user.user_id)
    return {"code": 200, "message": "OK", "data": {"deleted": True}}


@router.put(
    "/{answer_id}/favorite",
    summary="Favorite Answer",
)
async def favorite_answer(
    question_id: Annotated[int, Path(ge=0)],
    answer_id: Annotated[int, Path(ge=0)],
    auth_user: AuthUserInfo = Depends(get_auth_user),
    service: AnswersService = Depends(get_answers_service),
) -> dict:
    _ = question_id
    result = await service.add_favorite(answer_id=answer_id, user_id=auth_user.user_id)
    return {"code": 200, "message": "OK", "data": result}


@router.delete(
    "/{answer_id}/favorite",
    summary="Unfavorite Answer",
)
async def unfavorite_answer(
    question_id: Annotated[int, Path(ge=0)],
    answer_id: Annotated[int, Path(ge=0)],
    auth_user: AuthUserInfo = Depends(get_auth_user),
    service: AnswersService = Depends(get_answers_service),
) -> dict:
    _ = question_id
    result = await service.remove_favorite(answer_id=answer_id, user_id=auth_user.user_id)
    return {"code": 200, "message": "OK", "data": result}


@router.post(
    "/{answer_id}/attitudes",
    summary="Attitude on Answer (Vote)",
)
async def attitude_answer(
    question_id: Annotated[int, Path(ge=0)],
    answer_id: Annotated[int, Path(ge=0)],
    payload: dict = Body(...),
    auth_user: AuthUserInfo = Depends(get_auth_user),
    service: AnswersService = Depends(get_answers_service),
) -> dict:
    _ = question_id
    attitude_type = payload.get("attitude_type", "UNDEFINED")
    vote_type = attitude_type if attitude_type in ("POSITIVE", "NEGATIVE") else None
    if vote_type is None:
        result = await service.remove_answer_vote(answer_id=answer_id, user_id=auth_user.user_id)
    else:
        result = await service.vote_answer(
            answer_id=answer_id, user_id=auth_user.user_id, vote_type=vote_type
        )
    attitudes = {
        "positive_count": result.get("upvotes", 0),
        "negative_count": result.get("downvotes", 0),
        "difference": result.get("upvotes", 0) - result.get("downvotes", 0),
        "user_attitude": attitude_type,
    }
    return {"code": 200, "message": "OK", "data": {"attitudes": attitudes}}
