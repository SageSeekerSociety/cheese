"""收藏、用户设置、用户列表。"""

from typing import TYPE_CHECKING, Annotated

from fastapi import (
    APIRouter,
    Body,
    Depends,
    Path,
    Query,
)

from app.auth.checker import require_auth_user
from app.auth.core import AuthUserInfo
from app.core.errors import (
    ForbiddenError,
)
from app.db.session import get_db
from app.domain.answers.repositories import AnswerRepository
from app.domain.questions.repositories import (
    QuestionRepository,
    QuestionTopicRepository,
)
from app.domain.user.repositories import (
    UserProfileRepository,
    UserRepository,
    UserStatisticsRepository,
)
from app.domain.user.services import (
    UserAuthService,
)

if TYPE_CHECKING:
    pass

router = APIRouter(prefix="/users", tags=["Users"])


@router.get(
    "/{userId}/favorites/questions",
    summary="List user favorite questions",
)
async def get_user_favorite_questions(
    user_id: Annotated[int, Path(ge=0, alias="userId")],
    page_start: int | None = Query(default=None, ge=0, alias="pageStart"),
    page_size: int = Query(default=20, ge=1, le=100, alias="pageSize"),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    db=Depends(get_db),
) -> dict:
    question_repo = QuestionRepository(session=db)
    topic_repo = QuestionTopicRepository(session=db)

    offset = page_start or 0
    rows, total = await question_repo.list_followed(
        user_id=user_id, limit=page_size, offset=offset
    )
    topic_map = await topic_repo.list_topic_ids([row.id for row in rows])

    questions = []
    for row in rows:
        created_at_ms = int(row.created_at.timestamp() * 1000) if row.created_at else 0
        updated_at_ms = int(row.updated_at.timestamp() * 1000) if row.updated_at else 0
        dto = {
            "id": row.id,
            "title": row.title,
            "content": None,
            "type": row.type,
            "groupId": row.group_id,
            "bounty": row.bounty,
            "acceptedAnswerId": row.accepted_answer_id,
            "createdBy": row.created_by_id,
            "createdAt": created_at_ms,
            "updatedAt": updated_at_ms,
            "topicIds": topic_map.get(row.id, []),
        }
        questions.append(dto)

    returned = len(questions)
    has_more = offset + returned < total
    next_start = offset + returned if has_more and returned > 0 else None
    page = {
        "pageStart": offset,
        "pageSize": returned,
        "hasMore": has_more,
        "nextStart": next_start,
        "total": total,
    }
    return {
        "code": 200,
        "message": "OK",
        "data": {
            "questions": questions,
            "page": page,
        },
    }


@router.get(
    "/{userId}/favorites/answers",
    summary="List user favorite answers",
)
async def get_user_favorite_answers(
    user_id: Annotated[int, Path(ge=0, alias="userId")],
    page_start: int | None = Query(default=None, ge=0, alias="pageStart"),
    page_size: int = Query(default=20, ge=1, le=100, alias="pageSize"),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    db=Depends(get_db),
) -> dict:
    answer_repo = AnswerRepository(session=db)
    profile_repo = UserProfileRepository(session=db)

    offset = page_start or 0
    rows, total = await answer_repo.list_favorites_by_user(
        user_id=user_id, limit=page_size, offset=offset
    )

    answers = []
    for row in rows:
        created_at_ms = int(row.created_at.timestamp() * 1000) if row.created_at else 0
        updated_at_ms = int(row.updated_at.timestamp() * 1000) if row.updated_at else 0
        profile = await profile_repo.get_profile_by_user_id(row.created_by_id)
        sender = None
        if profile:
            sender = {
                "id": profile.user_id,
                "nickname": profile.nickname,
                "avatarId": profile.avatar_id,
                "intro": profile.intro,
            }
        dto = {
            "id": row.id,
            "questionId": row.question_id,
            "content": row.content,
            "createdBy": row.created_by_id,
            "sender": sender,
            "createdAt": created_at_ms,
            "updatedAt": updated_at_ms,
        }
        answers.append(dto)

    returned = len(answers)
    has_more = offset + returned < total
    next_start = offset + returned if has_more and returned > 0 else None
    page = {
        "pageStart": offset,
        "pageSize": returned,
        "hasMore": has_more,
        "nextStart": next_start,
        "total": total,
    }
    return {
        "code": 200,
        "message": "OK",
        "data": {
            "answers": answers,
            "page": page,
        },
    }


@router.get(
    "/{userId}/settings",
    summary="Get User Settings",
)
async def get_user_settings(
    user_id: Annotated[int, Path(ge=0, alias="userId")],
    auth_user: AuthUserInfo = Depends(require_auth_user),
) -> dict:
    if auth_user.user_id != user_id:
        raise ForbiddenError("Only the user themselves can view their settings.")
    settings = {
        "emailNotification": True,
        "pushNotification": True,
        "theme": "light",
    }
    return {"code": 200, "message": "OK", "data": {"settings": settings}}


@router.patch(
    "/{userId}/settings",
    summary="Update User Settings",
)
async def update_user_settings(
    user_id: Annotated[int, Path(ge=0, alias="userId")],
    payload: dict = Body(...),
    auth_user: AuthUserInfo = Depends(require_auth_user),
) -> dict:
    if auth_user.user_id != user_id:
        raise ForbiddenError("Only the user themselves can update their settings.")
    settings = {
        "emailNotification": payload.get("emailNotification", True),
        "pushNotification": payload.get("pushNotification", True),
        "theme": payload.get("theme", "light"),
    }
    return {"code": 200, "message": "OK", "data": {"settings": settings}}


@router.get(
    "",
    summary="List users",
)
async def list_users(
    q: str | None = Query(default=None),
    page_start: int | None = Query(default=None, ge=0, alias="pageStart"),
    page_size: int = Query(default=20, ge=1, le=100, alias="pageSize"),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    db=Depends(get_db),
) -> dict:
    """List users with optional search query."""
    profile_repo = UserProfileRepository(session=db)
    user_repo = UserRepository(session=db)
    stats_repo = UserStatisticsRepository(session=db)
    auth_service = UserAuthService(
        user_repo=user_repo,
        profile_repo=profile_repo,
        stats_repo=stats_repo,
    )

    offset = page_start or 0
    profiles = await profile_repo.list_profiles(limit=page_size, offset=offset)

    if q:
        filtered_profiles = []
        for profile in profiles:
            user = await user_repo.get_by_id(profile.user_id)
            if user and (
                q.lower() in user.username.lower()
                or q.lower() in profile.nickname.lower()
            ):
                filtered_profiles.append(profile)
        profiles = filtered_profiles

    users = []
    for profile in profiles:
        user = await user_repo.get_by_id(profile.user_id)
        if user:
            dto = await auth_service.build_user_dto(
                user=user,
                profile=profile,
                viewer_id=auth_user.user_id if auth_user.user_id > 0 else None,
            )
            users.append(dto)

    returned = len(users)
    page = {
        "pageStart": offset,
        "pageSize": returned,
        "hasMore": returned == page_size,
        "nextStart": offset + returned if returned == page_size else None,
    }
    return {
        "code": 200,
        "message": "OK",
        "data": {
            "users": users,
            "page": page,
        },
    }
