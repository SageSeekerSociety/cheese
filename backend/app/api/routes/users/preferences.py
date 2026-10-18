"""收藏、用户设置、用户列表。"""

from typing import TYPE_CHECKING, Annotated

from fastapi import (
    APIRouter,
    Body,
    Depends,
    Path,
    Query,
)

from app.api.response import ok
from app.api.write_access import ROUTE_DECIDES
from app.auth.checker import require_auth_user
from app.auth.core import AuthUserInfo
from app.core.errors import (
    ForbiddenError,
)
from app.db.session import get_db
from app.domain.answers.repositories import AnswerRepository
from app.domain.dashboard.user_statistics import UserStatisticsRepository
from app.domain.questions.repositories import (
    QuestionRepository,
    QuestionTopicRepository,
)
from app.domain.questions.services import question_to_dto
from app.domain.user.repositories import (
    UserProfileRepository,
    UserRepository,
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
        dto = question_to_dto(row, include_content=False)
        dto["topicIds"] = topic_map.get(row.id, [])
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
    return ok(
        {
            "questions": questions,
            "page": page,
        }
    )


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

    # 头像只给这位作者**自己挑过**的那张：``profile.avatar_id`` 是原始值，注册时人人
    # 被写上全局默认那一行，直接回它会让所有没挑过的人共用一张脸。判据只有一处
    # （``UserProfileRepository.chosen_avatar_ids``），没挑过的人不在映射里，回 null，
    # 由前端的 UserAvatar 画彩色首字母。
    author_ids = [row.created_by_id for row in rows if row.created_by_id is not None]
    chosen_ids = await profile_repo.chosen_avatar_ids(author_ids)
    # 作者档案一次取完，循环里查表：一页多少条回答都只多一次往返。
    profiles_by_user_id = await profile_repo.get_profiles_by_user_ids(author_ids)

    answers = []
    for row in rows:
        created_at_ms = int(row.created_at.timestamp() * 1000) if row.created_at else 0
        updated_at_ms = int(row.updated_at.timestamp() * 1000) if row.updated_at else 0
        profile = profiles_by_user_id.get(row.created_by_id)
        sender = None
        if profile:
            sender = {
                "id": profile.user_id,
                "nickname": profile.nickname,
                "avatarId": chosen_ids.get(profile.user_id),
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
    return ok(
        {
            "answers": answers,
            "page": page,
        }
    )


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
    return ok({"settings": settings})


@router.patch(
    "/{userId}/settings", summary="Update User Settings", dependencies=[ROUTE_DECIDES]
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
    return ok({"settings": settings})


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
    # 一页的账号一次取完；下面的搜索和组装都查这张表，页大小不该决定往返次数。
    users_by_id = await user_repo.get_by_ids([profile.user_id for profile in profiles])

    if q:
        filtered_profiles = []
        for profile in profiles:
            user = users_by_id.get(profile.user_id)
            if user and (
                q.lower() in user.username.lower()
                or q.lower() in profile.nickname.lower()
            ):
                filtered_profiles.append(profile)
        profiles = filtered_profiles

    # 这一列头像和名册同一条契约：只给本人**自己挑过**的那张，没挑过回 null。判据只有
    # 一处（``UserProfileRepository.chosen_avatar_ids``）。``build_user_dto`` 回的是原始
    # ``profile.avatar_id``（那是登录者自己的记录，前端靠 ``isChosenAvatar`` 判），
    # 列表这里按名册的规矩把全局默认那一张判掉，否则一列人共用同一张脸。
    chosen_ids = await profile_repo.chosen_avatar_ids([p.user_id for p in profiles])

    users = []
    for profile in profiles:
        user = users_by_id.get(profile.user_id)
        if user:
            dto = await auth_service.build_user_dto(
                user=user,
                profile=profile,
                viewer_id=auth_user.user_id if auth_user.user_id > 0 else None,
            )
            dto["avatarId"] = chosen_ids.get(user.id)
            users.append(dto)

    returned = len(users)
    page = {
        "pageStart": offset,
        "pageSize": returned,
        "hasMore": returned == page_size,
        "nextStart": offset + returned if returned == page_size else None,
    }
    return ok(
        {
            "users": users,
            "page": page,
        }
    )
