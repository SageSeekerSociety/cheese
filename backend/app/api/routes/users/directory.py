"""查人，以及一个人的提问 / 回答 / 关注列表。"""

import asyncio
from typing import TYPE_CHECKING, Annotated

from fastapi import (
    APIRouter,
    Depends,
    Path,
    Query,
)

from app.api.response import ok
from app.auth.checker import require_auth_user
from app.auth.core import AuthUserInfo
from app.core.config import settings
from app.core.errors import (
    NotFoundError,
    QuotaExceededError,
)
from app.core.redis import get_redis_client
from app.db.session import get_db
from app.domain.answers.repositories import AnswerRepository
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
    lookup_account,
)

if TYPE_CHECKING:
    pass

router = APIRouter(prefix="/users", tags=["Users"])

#: Lookups one account may make per window. An invitation dialog asks a handful
#: of times; a script walking a list of leaked emails to learn which of them
#: have an account here asks thousands, and the general request limit (about
#: twenty a second) would let it through in minutes.
LOOKUPS_PER_WINDOW = 60
LOOKUP_WINDOW_S = 3600
_LOOKUP_KEY = "cheesex:users-lookup:{}"


async def _spend_a_lookup(user_id: int) -> None:
    """Count one lookup against the caller's hourly budget; refuse past it.

    A Redis that is down or silent lets the lookup through, within the same
    wait `request_limits` gives it: a broken brake must not stop people
    inviting each other.
    """
    redis = get_redis_client()
    if redis is None:
        return
    key = _LOOKUP_KEY.format(user_id)
    try:
        async with asyncio.timeout(settings.request_limit_redis_timeout_s):
            used = await redis.incr(key)
            # On every call, not only the one that created the key: a key whose
            # EXPIRE was lost (the connection dropped after INCR, or the process
            # died in between) would otherwise refuse this account long after
            # the window, for good. `nx` keeps the window fixed at the first
            # lookup instead of sliding it forward on every call.
            await redis.expire(key, LOOKUP_WINDOW_S, nx=True)
    except Exception:  # a broken brake lets it through, see the docstring
        return
    if used > LOOKUPS_PER_WINDOW:
        raise QuotaExceededError()


@router.get("/lookup", summary="Find one account by exact username or email")
async def lookup_account_route(
    q: str = Query(..., min_length=1, max_length=254),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    db=Depends(get_db),
) -> dict:
    """The one person an external-member invitation is about to go to.

    Exact username or email only, like finding an external contact: no partial
    match, so the endpoint cannot be used to list who is registered, and a
    budget per account (`LOOKUPS_PER_WINDOW`), so it cannot be used to test a
    long list of addresses one by one either.
    """
    await _spend_a_lookup(auth_user.user_id)
    found = await lookup_account(db, q)
    if found is None:
        raise NotFoundError("No account with that username or email")
    return ok(found)


@router.get(
    "/{userId}/follow/questions",
    summary="List questions followed by user",
)
async def get_user_followed_questions(
    user_id: Annotated[int, Path(ge=1, alias="userId")],
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
        },
        "Query followed questions successfully.",
    )


@router.get(
    "/{userId}/questions",
    summary="List questions asked by user",
)
async def get_user_questions(
    user_id: Annotated[int, Path(alias="userId")],
    page_start: int | None = Query(default=None, ge=0, alias="pageStart"),
    page_size: int = Query(default=20, ge=1, le=100, alias="pageSize"),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    db=Depends(get_db),
) -> dict:
    if user_id < 1:
        raise NotFoundError("User not found")
    user_repo = UserRepository(session=db)
    target = await user_repo.get_by_id(user_id)
    if target is None:
        raise NotFoundError("User not found")
    question_repo = QuestionRepository(session=db)
    topic_repo = QuestionTopicRepository(session=db)

    offset = page_start or 0
    rows, total = await question_repo.list_by_user(
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
        },
        "Query asked questions successfully.",
    )


@router.get(
    "/{userId}/answers",
    summary="List answers posted by user",
)
async def get_user_answers(
    user_id: Annotated[int, Path(alias="userId")],
    page_start: int | None = Query(default=None, ge=0, alias="pageStart"),
    page_size: int = Query(default=20, ge=1, le=100, alias="pageSize"),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    db=Depends(get_db),
) -> dict:
    if user_id < 1:
        raise NotFoundError("User not found")
    user_repo = UserRepository(session=db)
    target = await user_repo.get_by_id(user_id)
    if target is None:
        raise NotFoundError("User not found")

    answer_repo = AnswerRepository(session=db)
    profile_repo = UserProfileRepository(session=db)

    all_ids = await answer_repo.list_all_answer_ids_by_user(user_id)

    if page_start is not None:
        try:
            start_idx = all_ids.index(page_start)
        except ValueError:
            start_idx = 0
    else:
        start_idx = 0

    end_idx = start_idx + page_size
    page_ids = all_ids[start_idx:end_idx]

    cursor = page_start if page_start else (all_ids[0] if all_ids else None)
    rows, total = await answer_repo.list_by_user(
        user_id=user_id, limit=page_size, cursor=cursor
    )

    # 只有真挑过头像才带 avatarId，判据是头像类型不是 ``default``（挂到非默认那张脸
    # 才算数）。直接抛 ``profile.avatar_id`` 会把「没挑过 = 存了默认脸」当成挑过，
    # 前端就退回全站默认头像了。档案和挑过的头像出自同一次读取。
    profiles, chosen = await profile_repo.profiles_and_chosen_avatars([user_id])
    profile = profiles.get(user_id)
    sender = None
    if profile:
        sender = {
            "id": profile.user_id,
            "nickname": profile.nickname,
            "avatarId": chosen.get(user_id),
            "intro": profile.intro,
        }

    answers = []
    for row in rows:
        created_at_ms = int(row.created_at.timestamp() * 1000) if row.created_at else 0
        updated_at_ms = int(row.updated_at.timestamp() * 1000) if row.updated_at else 0
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
    has_more = end_idx < len(all_ids)
    next_start = all_ids[end_idx] if has_more else 0

    first_id = page_ids[0] if page_ids else 0
    page = {
        "pageStart": first_id,
        "pageSize": returned,
        "hasMore": has_more,
        "nextStart": next_start,
        "total": total,
    }
    return ok(
        {
            "answers": answers,
            "page": page,
        },
        "Query answered questions successfully.",
    )
