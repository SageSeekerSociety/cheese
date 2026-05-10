from typing import Annotated

from fastapi import APIRouter, Body, Depends, Path, Query

from app.auth.checker import require_auth_user
from app.auth.core import AuthUserInfo
from app.core.errors import BadRequestError, NotFoundError
from app.db.session import get_db
from app.domain.comments.repositories import CommentRepository
from app.domain.comments.services import CommentService
from app.domain.questions.repositories import QuestionRepository
from app.domain.user.repositories import (
    UserFollowingRepository,
    UserProfileRepository,
    UserRepository,
    UserStatisticsRepository,
)
from app.domain.user.services import UserAuthService

router = APIRouter(prefix="/comments", tags=["Comments"])


async def get_comment_service(db=Depends(get_db)) -> CommentService:
    repo = CommentRepository(session=db)
    user_repo = UserRepository(session=db)
    profile_repo = UserProfileRepository(session=db)
    return CommentService(repo=repo, user_repo=user_repo, profile_repo=profile_repo)


async def get_user_auth_service(db=Depends(get_db)) -> UserAuthService:
    user_repo = UserRepository(session=db)
    profile_repo = UserProfileRepository(session=db)
    follow_repo = UserFollowingRepository(session=db)
    stats_repo = UserStatisticsRepository(session=db)
    return UserAuthService(
        user_repo=user_repo,
        profile_repo=profile_repo,
        follow_repo=follow_repo,
        stats_repo=stats_repo,
    )


# NOTE: Routes with more specific patterns must come BEFORE generic ones
# /{commentId}/attitudes must be before /{commentableType}/{commentableId}


@router.post(
    "/{commentId}/attitudes",
    summary="Update Attitude To Comment",
)
async def update_attitude_to_comment(
    commentId: Annotated[int, Path(ge=0)],
    payload: dict = Body(...),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: CommentService = Depends(get_comment_service),
) -> dict:
    attitude_type = payload.get("attitude_type", "UNDEFINED")
    vote_type = attitude_type if attitude_type in ("POSITIVE", "NEGATIVE") else None
    if vote_type is None:
        result = await service.remove_comment_vote(comment_id=commentId, user_id=auth_user.user_id)
    else:
        result = await service.vote_comment(
            comment_id=commentId, user_id=auth_user.user_id, vote_type=vote_type
        )
    attitudes = {
        "positive_count": result.get("upvotes", 0),
        "negative_count": result.get("downvotes", 0),
        "difference": result.get("upvotes", 0) - result.get("downvotes", 0),
        "user_attitude": attitude_type,
    }
    return {
        "code": 200,
        "message": "You have expressed your attitude towards the comment",
        "data": {"attitudes": attitudes},
    }


@router.get(
    "/{commentId}",
    summary="Get Comment By ID",
)
async def get_comment_by_id(
    commentId: Annotated[int, Path(ge=0)],
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: CommentService = Depends(get_comment_service),
    auth_service: UserAuthService = Depends(get_user_auth_service),
) -> dict:
    comment_dto = await service.get_comment(commentId, viewer_id=auth_user.user_id)
    # Upgrade the author's User dto to the richer build_user_dto shape (with
    # follow/fans/question/answer counts) so /comments/{id} matches /users/{id}.
    if comment_dto.get("created_by_id"):
        author_id = comment_dto["created_by_id"]
        user = await auth_service._user_repo.get_by_id(author_id)
        profile = await auth_service._profile_repo.get_profile_by_user_id(author_id)
        if user and profile:
            comment_dto["user"] = await auth_service.build_user_dto(
                user, profile, viewer_id=auth_user.user_id
            )
    return {"code": 200, "message": "OK", "data": {"comment": comment_dto}}


@router.patch(
    "/{commentId}",
    summary="Update Comment",
)
async def update_comment(
    commentId: Annotated[int, Path(ge=0)],
    payload: dict = Body(...),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: CommentService = Depends(get_comment_service),
) -> dict:
    content = payload.get("content")
    if not isinstance(content, str) or not content.strip():
        raise BadRequestError("content is required")
    comment = await service.update_comment(
        comment_id=commentId,
        user_id=auth_user.user_id,
        content=content,
    )
    return {"code": 200, "message": "OK", "data": {"comment": comment}}


@router.delete(
    "/{commentId}",
    summary="Delete Comment",
)
async def delete_comment(
    commentId: Annotated[int, Path(ge=0)],
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: CommentService = Depends(get_comment_service),
) -> dict:
    await service.delete_comment(comment_id=commentId, user_id=auth_user.user_id)
    return {"code": 200, "message": "Comment deleted successfully", "data": None}


@router.get(
    "/{commentableType}/{commentableId}",
    summary="Get Comments",
)
async def get_comments(
    commentableType: str,
    commentableId: Annotated[int, Path(ge=0)],
    page_start: int | None = Query(default=None),
    page_size: int = Query(default=20, ge=1, le=100),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: CommentService = Depends(get_comment_service),
) -> dict:
    comments, page = await service.list_comments(
        commentable_type=commentableType.upper(),
        commentable_id=commentableId,
        page_start=page_start,
        page_size=page_size,
        viewer_id=auth_user.user_id,
    )
    return {
        "code": 200,
        "message": "Get comments successfully",
        "data": {"comments": comments, "page": page},
    }


@router.post(
    "/{commentableType}/{commentableId}",
    summary="Create Comment",
    status_code=201,
)
async def create_comment(
    commentableType: str,
    commentableId: Annotated[int, Path(ge=0)],
    payload: dict = Body(...),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: CommentService = Depends(get_comment_service),
    db=Depends(get_db),
) -> dict:
    content = payload.get("content")
    if not isinstance(content, str) or not content.strip():
        raise BadRequestError("content is required")
    ctype = commentableType.upper()
    if ctype == "QUESTION":
        question_repo = QuestionRepository(session=db)
        question = await question_repo.get_by_id(commentableId)
        if question is None:
            raise NotFoundError("Question not found", data={"id": commentableId})
    result = await service.create_comment(
        commentable_type=ctype,
        commentable_id=commentableId,
        content=content,
        created_by_id=auth_user.user_id,
    )
    return {"code": 201, "message": "Comment created successfully", "data": result}
