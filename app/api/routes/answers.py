from __future__ import annotations

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
    question_id: Annotated[int, Path(ge=1)],
    page_start: int | None = Query(default=None, alias="page_start"),
    page_size: int = Query(default=20, ge=1, le=100, alias="page_size"),
    service: AnswersService = Depends(get_answers_service),
) -> dict:
    answers, page = await service.list_answers(
        question_id=question_id,
        page_start=page_start,
        page_size=page_size,
    )
    return {"code": 200, "message": "OK", "data": {"answers": answers, "page": page}}


@router.post(
    "",
    summary="Create Answer",
)
async def create_answer(
    question_id: Annotated[int, Path(ge=1)],
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
        "data": {"answer": answer},
    }


@router.post(
    "/{answer_id}/vote",
    summary="Vote on Answer",
)
async def vote_answer(
    question_id: Annotated[int, Path(ge=1)],
    answer_id: Annotated[int, Path(ge=1)],
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
    question_id: Annotated[int, Path(ge=1)],
    answer_id: Annotated[int, Path(ge=1)],
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
    question_id: Annotated[int, Path(ge=1)],
    answer_id: Annotated[int, Path(ge=1)],
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
    question_id: Annotated[int, Path(ge=1)],
    answer_id: Annotated[int, Path(ge=1)],
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
    question_id: Annotated[int, Path(ge=1)],
    answer_id: Annotated[int, Path(ge=1)],
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
    question_id: Annotated[int, Path(ge=1)],
    answer_id: Annotated[int, Path(ge=1)],
    comment_id: Annotated[int, Path(ge=1)],
    auth_user: AuthUserInfo = Depends(get_auth_user),
    discussion_service: DiscussionService = Depends(get_discussion_service),
) -> dict:
    _ = question_id
    _ = answer_id
    _ = auth_user
    await discussion_service.delete_discussion(comment_id)
    return {"code": 200, "message": "OK", "data": {"deleted": True}}
