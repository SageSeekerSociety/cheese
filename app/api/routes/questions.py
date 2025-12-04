from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Body

from app.auth.checker import get_auth_user, require_permission
from app.auth.core import Action, AuthUserInfo, Resource
from app.core.errors import BadRequestError
from app.db.session import get_db
from app.domain.answers.repositories import AnswerRepository
from app.domain.discussion.models import DiscussableModelType
from app.domain.discussion.reaction_services import DiscussionReactionService
from app.domain.discussion.repositories import (
    DiscussionReactionRepository,
    DiscussionRepository,
    ReactionTypeRepository,
)
from app.domain.discussion.services import DiscussionService
from app.domain.questions.repositories import QuestionRepository, QuestionTopicRepository
from app.domain.questions.services import QuestionsService
from app.domain.user.repositories import UserProfileRepository


router = APIRouter(prefix="/questions", tags=["Questions"])


async def get_questions_service(db=Depends(get_db)) -> QuestionsService:
    repo = QuestionRepository(session=db)
    topic_repo = QuestionTopicRepository(session=db)
    answer_repo = AnswerRepository(session=db)
    return QuestionsService(repo=repo, topic_repo=topic_repo, answer_repo=answer_repo)


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
    "/trending",
    summary="Get Trending Questions",
)
async def get_trending_questions(
    limit: int = Query(default=10, ge=1, le=50),
    days: int = Query(default=7, ge=1, le=30),
    service: QuestionsService = Depends(get_questions_service),
) -> dict:
    items = await service.get_trending_questions(limit=limit, days=days)
    return {"code": 200, "message": "OK", "data": {"questions": items}}


@router.get(
    "/stats",
    summary="Get Question Stats",
)
async def get_question_stats(
    service: QuestionsService = Depends(get_questions_service),
) -> dict:
    stats = await service.get_stats()
    return {"code": 200, "message": "OK", "data": stats}


@router.get(
    "/search-terms",
    summary="Get Popular Search Terms",
)
async def get_popular_search_terms(
    limit: int = Query(default=10, ge=1, le=50),
    days: int = Query(default=7, ge=1, le=30),
    service: QuestionsService = Depends(get_questions_service),
) -> dict:
    terms = await service.get_popular_search_terms(limit=limit, days=days)
    return {"code": 200, "message": "OK", "data": {"terms": terms}}


@router.get(
    "",
    summary="Search Questions",
)
async def search_questions(
    q: str | None = Query(default=None),
    page_start: int | None = Query(default=None, alias="page_start"),
    page_size: int = Query(default=20, ge=1, le=100, alias="page_size"),
    service: QuestionsService = Depends(get_questions_service),
) -> dict:
    items, page = await service.search_questions(
        keyword=q,
        page_size=page_size,
        page_start=page_start,
        sort_by="createdAt",
        sort_order="desc",
    )
    return {"code": 200, "message": "OK", "data": {"questions": items, "page": page}}


@router.post(
    "",
    summary="Create Question",
)
async def add_question(
    payload: dict = Body(...),
    auth_user: AuthUserInfo = Depends(get_auth_user),
    service: QuestionsService = Depends(get_questions_service),
) -> dict:
    title = payload.get("title") or ""
    content = payload.get("content") or ""
    type_ = int(payload.get("type", 0))
    group_id = payload.get("groupId")
    bounty = int(payload.get("bounty", 0))
    topic_ids = payload.get("topicIds") or []
    if not isinstance(topic_ids, list):
        raise BadRequestError("topicIds must be an array")
    topic_ints = [int(t) for t in topic_ids if isinstance(t, int) and t > 0]

    question = await service.create_question(
        user_id=auth_user.user_id,
        title=title,
        content=content,
        type_=type_,
        group_id=group_id,
        bounty=bounty,
        topic_ids=topic_ints,
    )
    return {"code": 201, "message": "Created", "data": {"question": question}}


@router.get(
    "/{question_id}",
    summary="Get Question",
)
async def get_question(
    question_id: Annotated[int, Path(ge=1)],
    auth_user: AuthUserInfo = Depends(get_auth_user),
    service: QuestionsService = Depends(get_questions_service),
) -> dict:
    user_id = auth_user.user_id if auth_user.user_id > 0 else None
    question = await service.get_question(question_id, user_id)
    return {"code": 200, "message": "OK", "data": {"question": question}}


@router.post(
    "/{question_id}/followers",
    summary="Follow Question",
)
async def follow_question(
    question_id: Annotated[int, Path(ge=1)],
    auth_user: AuthUserInfo = Depends(get_auth_user),
    service: QuestionsService = Depends(get_questions_service),
) -> dict:
    changed = await service.follow_question(question_id=question_id, user_id=auth_user.user_id)
    return {"code": 200, "message": "OK", "data": {"followed": changed}}


@router.delete(
    "/{question_id}/followers",
    summary="Unfollow Question",
)
async def unfollow_question(
    question_id: Annotated[int, Path(ge=1)],
    auth_user: AuthUserInfo = Depends(get_auth_user),
    service: QuestionsService = Depends(get_questions_service),
) -> dict:
    changed = await service.unfollow_question(question_id=question_id, user_id=auth_user.user_id)
    return {"code": 200, "message": "OK", "data": {"unfollowed": changed}}


@router.get(
    "/followed",
    summary="List Followed Questions",
)
async def list_followed_questions(
    pageStart: int | None = Query(default=None, alias="page_start"),
    pageSize: int = Query(default=20, ge=1, le=100, alias="page_size"),
    auth_user: AuthUserInfo = Depends(get_auth_user),
    service: QuestionsService = Depends(get_questions_service),
) -> dict:
    items, page = await service.list_followed(
        user_id=auth_user.user_id,
        page_size=pageSize,
        page_start=pageStart,
    )
    return {"code": 200, "message": "OK", "data": {"questions": items, "page": page}}


@router.post(
    "/{question_id}/accept/{answer_id}",
    summary="Accept Answer",
)
async def accept_answer(
    question_id: Annotated[int, Path(ge=1)],
    answer_id: Annotated[int, Path(ge=1)],
    auth_user: AuthUserInfo = require_permission(Action.ADMIN, Resource.QUESTION, "question_id"),
    service: QuestionsService = Depends(get_questions_service),
) -> dict:
    question = await service.accept_answer(
        question_id=question_id, answer_id=answer_id, user_id=auth_user.user_id
    )
    return {"code": 200, "message": "OK", "data": {"question": question}}


@router.delete(
    "/{question_id}/accept",
    summary="Unaccept Answer",
)
async def unaccept_answer(
    question_id: Annotated[int, Path(ge=1)],
    auth_user: AuthUserInfo = require_permission(Action.ADMIN, Resource.QUESTION, "question_id"),
    service: QuestionsService = Depends(get_questions_service),
) -> dict:
    question = await service.unaccept_answer(question_id=question_id, user_id=auth_user.user_id)
    return {"code": 200, "message": "OK", "data": {"question": question}}


@router.post(
    "/{question_id}/vote",
    summary="Vote on Question",
)
async def vote_question(
    question_id: Annotated[int, Path(ge=1)],
    payload: dict = Body(...),
    auth_user: AuthUserInfo = Depends(get_auth_user),
    service: QuestionsService = Depends(get_questions_service),
) -> dict:
    vote_type = payload.get("voteType", "UPVOTE")
    result = await service.vote_question(
        question_id=question_id, user_id=auth_user.user_id, vote_type=vote_type
    )
    return {"code": 200, "message": "OK", "data": result}


@router.delete(
    "/{question_id}/vote",
    summary="Remove Vote from Question",
)
async def remove_question_vote(
    question_id: Annotated[int, Path(ge=1)],
    auth_user: AuthUserInfo = Depends(get_auth_user),
    service: QuestionsService = Depends(get_questions_service),
) -> dict:
    result = await service.remove_question_vote(question_id=question_id, user_id=auth_user.user_id)
    return {"code": 200, "message": "OK", "data": result}


@router.get(
    "/{question_id}/vote",
    summary="Get Question Votes",
)
async def get_question_votes(
    question_id: Annotated[int, Path(ge=1)],
    auth_user: AuthUserInfo = Depends(get_auth_user),
    service: QuestionsService = Depends(get_questions_service),
) -> dict:
    user_id = auth_user.user_id if auth_user.user_id > 0 else None
    result = await service.get_question_votes(question_id=question_id, user_id=user_id)
    return {"code": 200, "message": "OK", "data": result}


@router.get(
    "/{question_id}/comments",
    summary="List Question Comments",
)
async def list_question_comments(
    question_id: Annotated[int, Path(ge=1)],
    page_start: int | None = Query(default=None, alias="page_start"),
    page_size: int = Query(default=20, ge=1, le=100, alias="page_size"),
    sort_by: str = Query(default="createdAt"),
    sort_order: str = Query(default="asc"),
    auth_user: AuthUserInfo = Depends(get_auth_user),
    discussion_service: DiscussionService = Depends(get_discussion_service),
) -> dict:
    user_id = auth_user.user_id if auth_user.user_id > 0 else None
    items, page = await discussion_service.list_discussions(
        model_type=DiscussableModelType.QUESTION.value,
        model_id=question_id,
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
    "/{question_id}/comments",
    summary="Create Question Comment",
)
async def create_question_comment(
    question_id: Annotated[int, Path(ge=1)],
    payload: dict = Body(...),
    auth_user: AuthUserInfo = Depends(get_auth_user),
    discussion_service: DiscussionService = Depends(get_discussion_service),
) -> dict:
    content = payload.get("content", "")
    parent_id = payload.get("parentId")
    mentioned_user_ids = payload.get("mentionedUserIds", [])
    comment = await discussion_service.create_discussion(
        user_id=auth_user.user_id,
        content=content,
        model_type=DiscussableModelType.QUESTION.value,
        model_id=question_id,
        parent_id=parent_id,
        mentioned_user_ids=mentioned_user_ids,
    )
    return {"code": 201, "message": "Created", "data": {"comment": comment}}


@router.delete(
    "/{question_id}/comments/{comment_id}",
    summary="Delete Question Comment",
)
async def delete_question_comment(
    question_id: Annotated[int, Path(ge=1)],
    comment_id: Annotated[int, Path(ge=1)],
    auth_user: AuthUserInfo = Depends(get_auth_user),
    discussion_service: DiscussionService = Depends(get_discussion_service),
) -> dict:
    _ = question_id
    _ = auth_user
    await discussion_service.delete_discussion(comment_id)
    return {"code": 200, "message": "OK", "data": {"deleted": True}}
