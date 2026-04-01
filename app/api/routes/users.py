from typing import Annotated

from fastapi import APIRouter, Body, Depends, Header, Path, Query, Request, Response, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.checker import get_auth_user
from app.auth.core import AuthUserInfo
from app.common.auth import (
    create_access_token,
    create_refresh_token,
    decode_token,
)
from app.core.errors import (
    AuthenticationRequiredError,
    BadRequestError,
    ForbiddenError,
    NotFoundError,
    UnprocessableEntityError,
)
from app.db.session import get_db
from app.domain.answers.repositories import AnswerRepository
from app.domain.oauth.repositories import OAuthConnectionRepository
from app.domain.oauth.services import OAuthService
from app.domain.passkey.repositories import PasskeyRepository
from app.domain.passkey.services import PasskeyService
from app.domain.questions.repositories import QuestionRepository, QuestionTopicRepository
from app.domain.team.membership_services import TeamMembershipService
from app.domain.team.repositories import TeamMembershipApplicationRepository, TeamRepository
from app.domain.team.services import TeamService
from app.domain.user.models import UserFollowingRelationship
from app.domain.user.realname_services import UserRealNameService
from app.domain.user.repositories import (
    UserFollowingRepository,
    UserProfileRepository,
    UserRealNameRepository,
    UserRepository,
    UserStatisticsRepository,
)
from app.domain.user.services import UserAuthService, UserProfileService

router = APIRouter(prefix="/users", tags=["Users"])


async def get_user_auth_service(
    db=Depends(get_db),
) -> UserAuthService:
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


async def get_user_profile_service(
    db=Depends(get_db),
) -> UserProfileService:
    profile_repo = UserProfileRepository(session=db)
    return UserProfileService(profile_repo=profile_repo)


async def get_team_membership_service(
    db=Depends(get_db),
) -> TeamMembershipService:
    team_repo = TeamRepository(session=db)
    app_repo = TeamMembershipApplicationRepository(session=db)
    return TeamMembershipService(session=db, team_repo=team_repo, application_repo=app_repo)


async def get_user_realname_service(
    db=Depends(get_db),
) -> UserRealNameService:
    user_repo = UserRepository(session=db)
    profile_repo = UserProfileRepository(session=db)
    realname_repo = UserRealNameRepository(session=db)
    return UserRealNameService(
        session=db,
        user_repo=user_repo,
        profile_repo=profile_repo,
        realname_repo=realname_repo,
    )


async def get_passkey_service(
    db=Depends(get_db),
) -> PasskeyService:
    repo = PasskeyRepository(session=db)
    return PasskeyService(repo=repo)


async def get_oauth_service(
    db=Depends(get_db),
):
    from redis.asyncio import Redis as AsyncRedis

    from app.core.config import settings

    repo = OAuthConnectionRepository(session=db)
    redis = AsyncRedis.from_url(settings.redis_url, decode_responses=True)
    try:
        yield OAuthService(repo=repo, redis=redis)
    finally:
        await redis.aclose()


@router.post(
    "/{userId}/followers",
    summary="Follow user",
    status_code=201,
)
async def follow_user(
    user_id: Annotated[int, Path(ge=1, alias="userId")],
    auth_user: AuthUserInfo = Depends(get_auth_user),
    db=Depends(get_db),
) -> dict:
    """Follow another user.

    NOTE: 当前实现不引入完整的权限模型，只做基础合法性检查：
    - 不能关注自己；
    - 要求被关注用户存在；
    - 重复关注直接视为错误返回 400。
    """
    if auth_user.user_id == user_id:
        raise UnprocessableEntityError("Cannot follow yourself")

    user_repo = UserRepository(session=db)
    follow_repo = UserFollowingRepository(session=db)

    target = await user_repo.get_by_id(user_id)
    if target is None:
        raise NotFoundError("User not found")

    if await follow_repo.is_following(auth_user.user_id, user_id):
        raise UnprocessableEntityError("User already followed")

    await follow_repo.add_follow(auth_user.user_id, user_id)
    follow_count = await follow_repo.count_following(auth_user.user_id)

    return {
        "code": 201,
        "message": "Follow user successfully.",
        "data": {
            "follow_count": follow_count,
        },
    }


@router.post(
    "/me/team-requests",
    summary="Create team join request",
)
async def create_team_join_request(
    body: dict,
    auth_user: AuthUserInfo = Depends(get_auth_user),
    membership_service: TeamMembershipService = Depends(get_team_membership_service),
) -> dict:
    team_id = body.get("teamId")
    if not isinstance(team_id, int) or team_id <= 0:
        raise BadRequestError("teamId must be a positive integer")
    message = body.get("message")
    app = await membership_service.create_team_join_request(
        user_id=auth_user.user_id,
        team_id=team_id,
        message=message,
    )
    return {
        "code": 200,
        "message": "OK",
        "data": {
            "application": {
                "id": app.id,
                "userId": app.user_id,
                "teamId": app.team_id,
                "type": app.type,
                "status": app.status,
                "role": app.role,
                "message": app.message,
            },
        },
    }


@router.delete(
    "/me/team-requests/{requestId}",
    summary="Cancel my pending join request",
)
async def cancel_my_join_request(
    request_id: Annotated[int, Path(ge=1, alias="requestId")],
    auth_user: AuthUserInfo = Depends(get_auth_user),
    membership_service: TeamMembershipService = Depends(get_team_membership_service),
) -> Response:
    await membership_service.cancel_my_join_request(
        user_id=auth_user.user_id, request_id=request_id
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "/me/teams/{teamId}",
    summary="Leave Team",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def leave_team(
    team_id: Annotated[int, Path(ge=1, alias="teamId")],
    auth_user: AuthUserInfo = Depends(get_auth_user),
    db=Depends(get_db),
) -> None:
    team_repo = TeamRepository(session=db)
    team_service = TeamService(team_repo)
    await team_service.remove_team_member(
        team_id=team_id,
        target_user_id=auth_user.user_id,
        actor_user_id=auth_user.user_id,
    )


@router.post(
    "/me/team-invitations/{invitationId}/accept",
    summary="Accept a team invitation",
)
async def accept_team_invitation(
    invitation_id: Annotated[int, Path(ge=1, alias="invitationId")],
    auth_user: AuthUserInfo = Depends(get_auth_user),
    membership_service: TeamMembershipService = Depends(get_team_membership_service),
) -> Response:
    await membership_service.accept_team_invitation(
        user_id=auth_user.user_id, invitation_id=invitation_id
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/me/team-invitations/{invitationId}/decline",
    summary="Decline a team invitation",
)
async def decline_team_invitation(
    invitation_id: Annotated[int, Path(ge=1, alias="invitationId")],
    auth_user: AuthUserInfo = Depends(get_auth_user),
    membership_service: TeamMembershipService = Depends(get_team_membership_service),
) -> Response:
    await membership_service.decline_team_invitation(
        user_id=auth_user.user_id, invitation_id=invitation_id
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/me/team-requests",
    summary="List my team join requests",
)
async def list_my_team_requests(
    status: str | None = Query(default=None),
    pageStart: int | None = Query(default=None),
    pageSize: int | None = Query(default=None),
    auth_user: AuthUserInfo = Depends(get_auth_user),
    membership_service: TeamMembershipService = Depends(get_team_membership_service),
) -> dict:
    status_enum = None
    if status is not None:
        upper = status.upper()
        if upper in {"PENDING", "APPROVED", "REJECTED", "ACCEPTED", "DECLINED", "CANCELED"}:
            from app.domain.team.models import ApplicationStatus

            status_enum = ApplicationStatus[upper]
        else:
            raise BadRequestError(f"Invalid status: {status}")
    apps, page = await membership_service.list_my_join_requests(
        user_id=auth_user.user_id,
        status=status_enum,
        page_start=pageStart,
        page_size=pageSize,
    )
    items = [
        {
            "id": app.id,
            "userId": app.user_id,
            "teamId": app.team_id,
            "type": app.type,
            "status": app.status,
            "role": app.role,
            "message": app.message,
        }
        for app in apps
    ]
    return {
        "code": 200,
        "message": "OK",
        "data": {
            "requests": items,
            "page": page,
        },
    }


@router.get(
    "/me/team-invitations",
    summary="List my team invitations",
)
async def list_my_team_invitations(
    status: str | None = Query(default=None),
    pageStart: int | None = Query(default=None),
    pageSize: int | None = Query(default=None),
    auth_user: AuthUserInfo = Depends(get_auth_user),
    membership_service: TeamMembershipService = Depends(get_team_membership_service),
) -> dict:
    status_enum = None
    if status is not None:
        upper = status.upper()
        if upper in {"PENDING", "APPROVED", "REJECTED", "ACCEPTED", "DECLINED", "CANCELED"}:
            from app.domain.team.models import ApplicationStatus

            status_enum = ApplicationStatus[upper]
        else:
            raise BadRequestError(f"Invalid status: {status}")
    apps, page = await membership_service.list_my_invitations(
        user_id=auth_user.user_id,
        status=status_enum,
        page_start=pageStart,
        page_size=pageSize,
    )
    items = [
        {
            "id": app.id,
            "userId": app.user_id,
            "teamId": app.team_id,
            "team": {"id": app.team_id},
            "type": app.type,
            "status": app.status,
            "role": app.role,
            "message": app.message,
        }
        for app in apps
    ]
    return {
        "code": 200,
        "message": "OK",
        "data": {
            "invitations": items,
            "page": page,
        },
    }


@router.delete(
    "/{userId}/followers",
    summary="Unfollow user",
)
async def unfollow_user(
    user_id: Annotated[int, Path(ge=1, alias="userId")],
    auth_user: AuthUserInfo = Depends(get_auth_user),
    db=Depends(get_db),
) -> dict:
    """Unfollow a previously followed user."""
    if auth_user.user_id == user_id:
        raise UnprocessableEntityError("Cannot unfollow yourself")

    user_repo = UserRepository(session=db)
    follow_repo = UserFollowingRepository(session=db)

    target = await user_repo.get_by_id(user_id)
    if target is None:
        raise NotFoundError("User not found")

    removed = await follow_repo.soft_delete_follow(auth_user.user_id, user_id)
    if not removed:
        raise UnprocessableEntityError("User not followed yet")

    follow_count = await follow_repo.count_following(auth_user.user_id)
    return {
        "code": 200,
        "message": "Unfollow user successfully.",
        "data": {
            "follow_count": follow_count,
        },
    }


@router.get(
    "/{userId}/followers",
    summary="List followers of a user",
)
async def get_followers(
    user_id: Annotated[int, Path(ge=1, alias="userId")],
    page_start: int | None = Query(default=None, alias="page_start"),
    page_size: int = Query(default=20, ge=1, le=200, alias="page_size"),
    auth_user: AuthUserInfo = Depends(get_auth_user),
    db=Depends(get_db),
) -> dict:
    """Return followers of the given user (cursor-based pagination)."""
    if page_size <= 0:
        page_size = 20

    user_repo = UserRepository(session=db)
    profile_repo = UserProfileRepository(session=db)
    follow_repo = UserFollowingRepository(session=db)
    stats_repo = UserStatisticsRepository(session=db)
    auth_service = UserAuthService(
        user_repo=user_repo,
        profile_repo=profile_repo,
        follow_repo=follow_repo,
        stats_repo=stats_repo,
    )

    target = await user_repo.get_by_id(user_id)
    if target is None:
        raise NotFoundError("User not found")

    all_follower_ids_stmt = (
        select(UserFollowingRelationship.follower_id)
        .where(
            UserFollowingRelationship.followee_id == user_id,
            UserFollowingRelationship.deleted_at.is_(None),
        )
        .order_by(UserFollowingRelationship.follower_id.asc())
    )
    all_result = await db.execute(all_follower_ids_stmt)
    all_follower_ids = [r[0] for r in all_result.all()]

    if page_start is not None:
        try:
            start_idx = all_follower_ids.index(page_start)
        except ValueError:
            start_idx = 0
    else:
        start_idx = 0

    end_idx = start_idx + page_size
    page_follower_ids = all_follower_ids[start_idx:end_idx]

    followers: list[dict] = []
    for fid in page_follower_ids:
        user = await user_repo.get_by_id(fid)
        profile = await profile_repo.get_profile_by_user_id(fid)
        if user is None or profile is None:
            continue
        followers.append(
            await auth_service.build_user_dto(
                user=user,
                profile=profile,
                viewer_id=auth_user.user_id if auth_user.user_id > 0 else None,
            )
        )

    returned = len(followers)
    has_prev = start_idx > 0
    prev_start = all_follower_ids[0] if has_prev and len(all_follower_ids) > 0 else 0
    has_more = end_idx < len(all_follower_ids)
    next_start = all_follower_ids[end_idx] if has_more else 0

    first_id = page_follower_ids[0] if page_follower_ids else 0
    page = {
        "page_start": first_id,
        "page_size": returned,
        "has_prev": has_prev,
        "prev_start": prev_start,
        "has_more": has_more,
        "next_start": next_start,
    }

    return {
        "code": 200,
        "message": "Query followers successfully.",
        "data": {
            "users": followers,
            "page": page,
        },
    }


@router.get(
    "/{userId}/follow/users",
    summary="List followees of a user",
)
async def get_followees(
    user_id: Annotated[int, Path(ge=1, alias="userId")],
    page_start: int | None = Query(default=None, alias="page_start"),
    page_size: int = Query(default=20, ge=1, le=200, alias="page_size"),
    auth_user: AuthUserInfo = Depends(get_auth_user),
    db=Depends(get_db),
) -> dict:
    """Return users that the given user is following (cursor-based pagination)."""
    if page_size <= 0:
        page_size = 20

    user_repo = UserRepository(session=db)
    profile_repo = UserProfileRepository(session=db)
    follow_repo = UserFollowingRepository(session=db)
    stats_repo = UserStatisticsRepository(session=db)
    auth_service = UserAuthService(
        user_repo=user_repo,
        profile_repo=profile_repo,
        follow_repo=follow_repo,
        stats_repo=stats_repo,
    )

    target = await user_repo.get_by_id(user_id)
    if target is None:
        raise NotFoundError("User not found")

    all_followee_ids_stmt = (
        select(UserFollowingRelationship.followee_id)
        .where(
            UserFollowingRelationship.follower_id == user_id,
            UserFollowingRelationship.deleted_at.is_(None),
        )
        .order_by(UserFollowingRelationship.followee_id.asc())
    )
    all_result = await db.execute(all_followee_ids_stmt)
    all_followee_ids = [r[0] for r in all_result.all()]

    if page_start is not None:
        try:
            start_idx = all_followee_ids.index(page_start)
        except ValueError:
            start_idx = 0
    else:
        start_idx = 0

    end_idx = start_idx + page_size
    page_followee_ids = all_followee_ids[start_idx:end_idx]

    followees: list[dict] = []
    for fid in page_followee_ids:
        user = await user_repo.get_by_id(fid)
        profile = await profile_repo.get_profile_by_user_id(fid)
        if user is None or profile is None:
            continue
        followees.append(
            await auth_service.build_user_dto(
                user=user,
                profile=profile,
                viewer_id=auth_user.user_id if auth_user.user_id > 0 else None,
            )
        )

    returned = len(followees)
    has_prev = start_idx > 0
    prev_start = all_followee_ids[0] if has_prev and len(all_followee_ids) > 0 else 0
    has_more = end_idx < len(all_followee_ids)
    next_start = all_followee_ids[end_idx] if has_more else 0

    first_id = page_followee_ids[0] if page_followee_ids else 0
    page = {
        "page_start": first_id,
        "page_size": returned,
        "has_prev": has_prev,
        "prev_start": prev_start,
        "has_more": has_more,
        "next_start": next_start,
    }

    return {
        "code": 200,
        "message": "Query followees successfully.",
        "data": {
            "users": followees,
            "page": page,
        },
    }


@router.get(
    "/{userId}/follow/questions",
    summary="List questions followed by user",
)
async def get_user_followed_questions(
    user_id: Annotated[int, Path(ge=1, alias="userId")],
    page_start: int | None = Query(default=None, alias="page_start"),
    page_size: int = Query(default=20, ge=1, le=100, alias="page_size"),
    auth_user: AuthUserInfo = Depends(get_auth_user),
    db=Depends(get_db),
) -> dict:
    question_repo = QuestionRepository(session=db)
    topic_repo = QuestionTopicRepository(session=db)

    offset = page_start or 0
    rows, total = await question_repo.list_followed(user_id=user_id, limit=page_size, offset=offset)
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
        "message": "Query followed questions successfully.",
        "data": {
            "questions": questions,
            "page": page,
        },
    }


@router.get(
    "/{userId}/questions",
    summary="List questions asked by user",
)
async def get_user_questions(
    user_id: Annotated[int, Path(alias="userId")],
    page_start: int | None = Query(default=None, alias="page_start"),
    page_size: int = Query(default=20, ge=1, le=100, alias="page_size"),
    auth_user: AuthUserInfo = Depends(get_auth_user),
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
    rows, total = await question_repo.list_by_user(user_id=user_id, limit=page_size, offset=offset)
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
        "message": "Query asked questions successfully.",
        "data": {
            "questions": questions,
            "page": page,
        },
    }


@router.get(
    "/{userId}/answers",
    summary="List answers posted by user",
)
async def get_user_answers(
    user_id: Annotated[int, Path(alias="userId")],
    page_start: int | None = Query(default=None, alias="page_start"),
    page_size: int = Query(default=20, ge=1, le=100, alias="page_size"),
    auth_user: AuthUserInfo = Depends(get_auth_user),
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

    offset = page_start or 0
    if offset < 0:
        offset = 0

    rows, total = await answer_repo.list_by_user(user_id=user_id, limit=page_size, offset=offset)

    profile = await profile_repo.get_profile_by_user_id(user_id)
    sender = None
    if profile:
        sender = {
            "id": profile.user_id,
            "nickname": profile.nickname,
            "avatarId": profile.avatar_id,
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
        "message": "Query answered questions successfully.",
        "data": {
            "answers": answers,
            "page": page,
        },
    }


@router.post(
    "/verify/email",
    summary="Send registration email verification code",
)
async def send_register_email_code(
    payload: dict,
    db=Depends(get_db),
) -> dict:
    """Send email verification code for registration.

    Uses Redis for code storage (10 min TTL) and sends via configured SMTP.
    Falls back to success response if email not configured (for dev).
    When inviteCode is provided, any email domain is accepted;
    otherwise only educational email addresses are allowed.
    """
    import re

    from redis.asyncio import Redis as AsyncRedis

    from app.core.config import settings
    from app.core.errors import ConflictError
    from app.domain.user.verification_service import EmailVerificationService

    email = payload.get("email")
    invite_code = payload.get("inviteCode")
    if not email:
        raise BadRequestError("email is required")

    email_regex = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
    if not re.match(email_regex, email):
        raise UnprocessableEntityError("Invalid email address format")

    # With a valid invite code, any email domain is allowed;
    # without one, only educational institutions.
    if not invite_code:
        allowed_suffixes = (".ruc.edu.cn", ".edu.cn", ".edu")
        if not any(email.endswith(suffix) for suffix in allowed_suffixes):
            raise UnprocessableEntityError("Email must be from an educational institution")

    user_repo = UserRepository(session=db)
    if await user_repo.is_email_taken(email):
        raise ConflictError("Email already registered")

    redis = AsyncRedis.from_url(settings.redis_url, decode_responses=False)
    try:
        service = EmailVerificationService(redis)
        await service.send_verification_code(email)
    finally:
        await redis.aclose()

    return {
        "code": 201,
        "message": "Send email successfully.",
    }


@router.get(
    "/registration-config",
    summary="Get registration configuration",
    openapi_extra={"x-public": True},
)
async def get_registration_config() -> dict:
    """Return public registration settings so the frontend can adapt its UI."""
    from app.core.config import settings

    return {
        "code": 200,
        "message": "Success",
        "data": {
            "requireInviteCode": settings.require_invite_code,
            "inviteCodeBypassesEmail": False,
        },
    }


@router.post(
    "",
    summary="Register User",
)
async def register_user(
    payload: dict,
    response: Response,
    auth_service: UserAuthService = Depends(get_user_auth_service),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """Registration flow with email verification.

    Supports:
    - Legacy password-based auth (isLegacyAuth=True, password required)
    - SRP auth (srpSalt/srpVerifier required)
    """
    import re

    from redis.asyncio import Redis as AsyncRedis

    from app.core.config import settings
    from app.domain.user.verification_service import EmailVerificationService

    username = payload.get("username")
    nickname = payload.get("nickname")
    email = payload.get("email")
    email_code = payload.get("emailCode")
    password = payload.get("password")
    srp_salt = payload.get("srpSalt")
    srp_verifier = payload.get("srpVerifier")
    invite_code = payload.get("inviteCode")
    _ = payload.get("isLegacyAuth", False)

    has_invite_code = bool(invite_code)
    if has_invite_code:
        from app.domain.invite.services import InviteCodeService

        invite_service = InviteCodeService(session)
        try:
            await invite_service.validate_code(invite_code)
        except ValueError as exc:
            msg = str(exc)
            error_map = {
                "INVALID_CODE": "Invalid invite code",
                "CODE_DISABLED": "This invite code has been disabled",
                "CODE_EXPIRED": "This invite code has expired",
                "CODE_EXHAUSTED": "This invite code has been fully used",
            }
            raise UnprocessableEntityError(error_map.get(msg, "Invalid invite code")) from exc

    if not username or not nickname or not email:
        raise BadRequestError("username, nickname, and email are required")
    if not email_code:
        raise BadRequestError("emailCode is required")

    username_pattern = r"^[a-zA-Z0-9_-]+$"
    if not re.match(username_pattern, username):
        raise UnprocessableEntityError("Invalid username format")

    nickname_pattern = r"^[^\s]+$"
    if not re.match(nickname_pattern, nickname):
        raise UnprocessableEntityError("Invalid nickname format")

    has_srp = srp_salt and srp_verifier
    has_password = bool(password)

    if not has_srp and not has_password:
        raise BadRequestError("Either password or srpSalt/srpVerifier is required")

    if has_password:
        password_pattern = r'^(?=.*[a-zA-Z])(?=.*[!@#$%^&*()_+\-=\[\]{};\':"\\|,.<>\/?]).{8,}$'
        if not re.match(password_pattern, password):
            raise UnprocessableEntityError(
                "Password must be at least 8 characters and contain letters and special characters"
            )

    # Always verify email code, regardless of invite code
    redis = AsyncRedis.from_url(settings.redis_url, decode_responses=False)
    try:
        service = EmailVerificationService(redis)
        is_valid = await service.verify_code(email, email_code)
        if not is_valid:
            raise UnprocessableEntityError("Invalid or expired verification code")
    finally:
        await redis.aclose()

    try:
        if has_password:
            user, profile = await auth_service.register_with_password(
                username=username,
                nickname=nickname,
                email=email,
                password=password,
            )
        else:
            user, profile = await auth_service.register_with_srp(
                username=username,
                nickname=nickname,
                email=email,
                srp_salt=srp_salt,
                srp_verifier=srp_verifier,
            )
    except ValueError as exc:
        msg = str(exc)
        if msg == "USERNAME_TAKEN":
            raise UnprocessableEntityError("Username already registered") from exc
        if msg == "EMAIL_TAKEN":
            raise UnprocessableEntityError("Email already registered") from exc
        raise

    # Consume invite code after successful registration
    if settings.require_invite_code and invite_code:
        from app.domain.invite.services import InviteCodeService

        invite_service = InviteCodeService(session)
        await invite_service.consume_code(invite_code)

    access_token = create_access_token(user.id)
    refresh_token = create_refresh_token(user.id)

    response.set_cookie(
        "REFRESH_TOKEN",
        refresh_token,
        httponly=True,
        samesite="lax",
        path="/users/auth",
    )

    user_dto = await auth_service.build_user_dto(
        user=user,
        profile=profile,
        viewer_id=user.id,
    )
    return {
        "code": 201,
        "message": "Register successfully.",
        "data": {
            "user": user_dto,
            "accessToken": access_token,
        },
    }


@router.get(
    "/me",
    summary="Get current user",
)
async def get_current_user(
    auth_user: AuthUserInfo = Depends(get_auth_user),
    auth_service: UserAuthService = Depends(get_user_auth_service),
) -> dict:
    """Return the profile of the current authenticated user."""
    try:
        user, profile = await auth_service.get_user_with_profile(auth_user.user_id)
    except ValueError:
        raise NotFoundError("User not found") from None

    user_dto = await auth_service.build_user_dto(
        user=user,
        profile=profile,
        viewer_id=auth_user.user_id,
    )
    return {
        "code": 200,
        "message": "Query current user successfully.",
        "data": {
            "user": user_dto,
        },
    }


@router.get(
    "/{userId}",
    summary="Get user by id",
)
async def get_user(
    user_id: Annotated[int, Path(alias="userId")],
    auth_user: AuthUserInfo = Depends(get_auth_user),
    auth_service: UserAuthService = Depends(get_user_auth_service),
) -> dict:
    """Return the public profile of a user."""
    if user_id < 1:
        raise NotFoundError("User not found")
    try:
        user, profile = await auth_service.get_user_with_profile(user_id)
    except ValueError:
        raise NotFoundError("User not found") from None

    user_dto = await auth_service.build_user_dto(
        user=user,
        profile=profile,
        viewer_id=auth_user.user_id if auth_user.user_id > 0 else None,
    )
    return {
        "code": 200,
        "message": "Query user successfully.",
        "data": {
            "user": user_dto,
        },
    }


@router.patch(
    "/{userId}",
    summary="Update user profile (partial)",
)
async def patch_user_profile(
    user_id: Annotated[int, Path(ge=0, alias="userId")],
    payload: dict = Body(...),
    auth_user: AuthUserInfo = Depends(get_auth_user),
    profile_service: UserProfileService = Depends(get_user_profile_service),
) -> dict:
    if auth_user.user_id != user_id:
        raise ForbiddenError("Only the user themselves can update their profile.")
    await profile_service.update_profile(
        user_id=user_id,
        nickname=payload.get("nickname"),
        intro=payload.get("intro"),
        avatar_id=payload.get("avatarId"),
    )
    return {"code": 200, "message": "Success", "data": {}}


@router.put(
    "/{userId}",
    summary="Update user profile (full)",
)
async def put_user_profile(
    user_id: Annotated[int, Path(ge=0, alias="userId")],
    payload: dict = Body(...),
    auth_user: AuthUserInfo = Depends(get_auth_user),
    profile_service: UserProfileService = Depends(get_user_profile_service),
) -> dict:
    if auth_user.user_id != user_id:
        raise ForbiddenError("Only the user themselves can update their profile.")
    await profile_service.update_profile(
        user_id=user_id,
        nickname=payload.get("nickname"),
        intro=payload.get("intro"),
        avatar_id=payload.get("avatarId"),
    )
    return {"code": 200, "message": "Success", "data": {}}


@router.get(
    "/auth/methods/{username}",
    summary="Get authentication methods for a user",
    description="Returns which auth methods a user supports. Returns safe defaults for non-existent users.",
    openapi_extra={"x-public": True},
)
async def get_auth_methods(
    username: str,
    session: AsyncSession = Depends(get_db),
) -> dict:
    """Return supported auth methods without revealing whether the user exists."""
    from redis.asyncio import Redis as AsyncRedis

    from app.core.config import settings
    from app.domain.user.login_security import TOTPService

    default_response = {
        "code": 200,
        "message": "Authentication methods retrieved successfully.",
        "data": {
            "supports_srp": False,
            "supports_passkey": False,
            "supports_2fa": False,
            "requires_2fa": False,
        },
    }

    from app.domain.user.models import User

    result = await session.execute(select(User).where(User.username == username))
    user = result.scalar_one_or_none()
    if user is None:
        return default_response

    # Check passkeys
    from app.domain.passkey.models import PasskeyCredential

    passkey_result = await session.execute(
        select(func.count()).select_from(PasskeyCredential).where(PasskeyCredential.user_id == user.id)
    )
    passkey_count = passkey_result.scalar() or 0

    # Check 2FA
    redis = AsyncRedis.from_url(settings.redis_url, decode_responses=False)
    try:
        totp_service = TOTPService(redis)
        has_2fa = await totp_service.is_2fa_enabled(user.id)
    finally:
        await redis.aclose()

    supports_srp = bool(user.hashed_password and user.hashed_password.startswith("SRP:"))

    return {
        "code": 200,
        "message": "Authentication methods retrieved successfully.",
        "data": {
            "supports_srp": supports_srp,
            "supports_passkey": passkey_count > 0,
            "supports_2fa": has_2fa,
            "requires_2fa": has_2fa,
        },
    }


@router.post(
    "/auth/login",
    summary="User Login",
)
async def user_login(
    payload: dict,
    request: Request,
    response: Response,
    auth_service: UserAuthService = Depends(get_user_auth_service),
) -> dict:
    from redis.asyncio import Redis as AsyncRedis

    from app.core.config import settings
    from app.domain.user.login_security import LoginRateLimiter, SessionManager, TOTPService

    username = payload.get("username")
    password = payload.get("password")
    totp_code = payload.get("totpCode")
    if not username or not password:
        raise BadRequestError("username and password are required")

    redis = AsyncRedis.from_url(settings.redis_url, decode_responses=False)
    try:
        rate_limiter = LoginRateLimiter(redis)

        if await rate_limiter.is_locked_out(username):
            remaining = await rate_limiter.get_remaining_lockout_seconds(username)
            raise ForbiddenError(f"Account locked. Try again in {remaining} seconds")

        auth_result = await auth_service.authenticate(username=username, password=password)
        if auth_result is None:
            attempts = await rate_limiter.record_failed_attempt(username)
            remaining_attempts = max(0, 5 - attempts)
            if remaining_attempts == 0:
                raise ForbiddenError("Account locked due to too many failed attempts")
            raise AuthenticationRequiredError(
                f"Invalid username or password. {remaining_attempts} attempts remaining"
            )

        user, profile = auth_result

        totp_service = TOTPService(redis)
        requires_2fa = await totp_service.is_2fa_enabled(user.id)

        if requires_2fa:
            if not totp_code:
                return {
                    "code": 200,
                    "message": "2FA required",
                    "data": {
                        "requires2FA": True,
                        "userId": user.id,
                    },
                }
            if not await totp_service.verify_2fa(user.id, totp_code):
                raise AuthenticationRequiredError("Invalid 2FA code")

        await rate_limiter.clear_attempts(username)

        session_manager = SessionManager(redis)
        client_ip = request.client.host if request.client else ""
        user_agent = request.headers.get("user-agent", "")
        session_id = await session_manager.create_session(
            user_id=user.id,
            ip_address=client_ip,
            user_agent=user_agent,
        )

        access_token = create_access_token(user.id)
        refresh_token = create_refresh_token(user.id)

        response.set_cookie(
            "REFRESH_TOKEN",
            refresh_token,
            httponly=True,
            samesite="lax",
            path="/users/auth",
        )
        response.set_cookie(
            "SESSION_ID",
            session_id,
            httponly=True,
            samesite="lax",
            path="/",
        )

        user_dto = await auth_service.build_user_dto(
            user=user,
            profile=profile,
            viewer_id=user.id,
        )
        return {
            "code": 201,
            "message": "Login successfully.",
            "data": {
                "user": user_dto,
                "accessToken": access_token,
                "requires2FA": False,
                "sessionId": session_id,
            },
        }
    finally:
        await redis.aclose()


@router.post(
    "/auth/srp/init",
    summary="SRP Login Step 1: Initialize",
)
async def srp_login_init(
    payload: dict,
    auth_service: UserAuthService = Depends(get_user_auth_service),
) -> dict:
    """SRP login step 1: return server ephemeral and salt."""
    import srptools as _srp
    from redis.asyncio import Redis as AsyncRedis

    from app.core.config import settings

    username = payload.get("username")
    if not username:
        raise BadRequestError("username is required")

    user = await auth_service._user_repo.get_by_username(username)
    if user is None or not user.hashed_password or not user.hashed_password.startswith("SRP:"):
        raise AuthenticationRequiredError("Invalid username or password")

    parts = user.hashed_password.split(":", 2)
    if len(parts) != 3:
        raise AuthenticationRequiredError("Invalid username or password")
    stored_salt, stored_verifier = parts[1], parts[2]

    try:
        server_ctx = _srp.SRPContext(username)
        server_session = _srp.SRPServerSession(server_ctx, stored_verifier)
    except (ValueError, _srp.SRPException):
        raise AuthenticationRequiredError("Invalid username or password") from None

    redis = AsyncRedis.from_url(settings.redis_url, decode_responses=True)
    try:
        srp_key = f"srp:login:{username}"
        await redis.setex(srp_key, 300, server_session.private)
    finally:
        await redis.aclose()

    return {
        "code": 200,
        "message": "SRP initialization.",
        "data": {
            "serverPublicEphemeral": server_session.public,
            "salt": stored_salt,
        },
    }


@router.post(
    "/auth/srp/verify",
    summary="SRP Login Step 2: Verify",
)
async def srp_login_verify(
    payload: dict,
    response: Response,
    auth_service: UserAuthService = Depends(get_user_auth_service),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """SRP login step 2: verify client proof and return tokens."""
    import srptools as _srp
    from redis.asyncio import Redis as AsyncRedis

    from app.common.auth import create_access_token, create_refresh_token
    from app.core.config import settings
    from app.domain.user.login_security import LoginRateLimiter, SessionManager, TOTPService

    username = payload.get("username")
    client_public = payload.get("clientPublicEphemeral")
    client_proof = payload.get("clientProof")
    totp_code = payload.get("totpCode")

    if not username or not client_public or not client_proof:
        raise BadRequestError("username, clientPublicEphemeral, and clientProof are required")

    user = await auth_service._user_repo.get_by_username(username)
    if user is None or not user.hashed_password or not user.hashed_password.startswith("SRP:"):
        raise AuthenticationRequiredError("Invalid username or password")

    parts = user.hashed_password.split(":", 2)
    if len(parts) != 3:
        raise AuthenticationRequiredError("Invalid username or password")
    stored_salt, stored_verifier = parts[1], parts[2]

    redis = AsyncRedis.from_url(settings.redis_url, decode_responses=True)
    try:
        rate_limiter = LoginRateLimiter(
            AsyncRedis.from_url(settings.redis_url, decode_responses=False)
        )

        if await rate_limiter.is_locked_out(username):
            remaining = await rate_limiter.get_remaining_lockout_seconds(username)
            raise ForbiddenError(f"Account locked. Try again in {remaining} seconds")

        srp_key = f"srp:login:{username}"
        server_private = await redis.get(srp_key)
        if not server_private:
            raise AuthenticationRequiredError("SRP session expired, please reinitialize")
        await redis.delete(srp_key)

        try:
            server_ctx = _srp.SRPContext(username)
            server_session = _srp.SRPServerSession(
                server_ctx, stored_verifier, private=server_private
            )
            server_session.process(client_public, stored_salt)
        except (ValueError, TypeError, _srp.SRPException):
            await rate_limiter.record_failed_attempt(username)
            raise AuthenticationRequiredError("Invalid username or password") from None

        if not server_session.verify_proof(client_proof):
            await rate_limiter.record_failed_attempt(username)
            raise AuthenticationRequiredError("Invalid username or password")

        # SRP verified — clear rate limiter
        await rate_limiter.clear_attempts(username)

        profile = await auth_service._profile_repo.get_profile_by_user_id(user.id)
        if profile is None:
            raise AuthenticationRequiredError("Invalid username or password")

        # Check 2FA
        totp_service = TOTPService(
            AsyncRedis.from_url(settings.redis_url, decode_responses=False)
        )
        requires_2fa = await totp_service.is_2fa_enabled(user.id)
        if requires_2fa and not totp_code:
            temp_token = create_access_token(user.id)
            return {
                "code": 200,
                "message": "2FA required",
                "data": {
                    "requires2FA": True,
                    "tempToken": temp_token,
                    "serverProof": "",
                },
            }

        if requires_2fa:
            is_valid_totp = await totp_service.verify_2fa(user.id, totp_code)
            if not is_valid_totp:
                raise AuthenticationRequiredError("Invalid 2FA code")

        proof = server_session.key_proof
        server_proof_str = proof.decode() if isinstance(proof, bytes) else str(proof)

        access_token = create_access_token(user.id)
        refresh_token = create_refresh_token(user.id)
        session_mgr = SessionManager(
            AsyncRedis.from_url(settings.redis_url, decode_responses=False)
        )
        session_id = await session_mgr.create_session(user.id)

        response.set_cookie(
            "REFRESH_TOKEN",
            refresh_token,
            httponly=True,
            samesite="lax",
            path="/users/auth",
        )
        response.set_cookie(
            "SESSION_ID",
            session_id,
            httponly=True,
            samesite="lax",
            path="/",
        )

        user_dto = await auth_service.build_user_dto(
            user=user,
            profile=profile,
            viewer_id=user.id,
        )
        return {
            "code": 201,
            "message": "Login successfully.",
            "data": {
                "user": user_dto,
                "accessToken": access_token,
                "serverProof": server_proof_str,
                "requires2FA": False,
                "sessionId": session_id,
            },
        }
    finally:
        await redis.aclose()


@router.post(
    "/auth/refresh-token",
    summary="Refresh Access Token",
)
async def refresh_access_token(
    request: Request,
    response: Response,
    auth_service: UserAuthService = Depends(get_user_auth_service),
) -> dict:
    refresh_token = request.cookies.get("REFRESH_TOKEN")
    if not refresh_token:
        raise AuthenticationRequiredError("Refresh token is missing")

    payload = decode_token(refresh_token)
    if payload.get("type") != "refresh":
        raise AuthenticationRequiredError("Invalid refresh token")

    try:
        user_id = int(payload.get("sub"))
    except (TypeError, ValueError) as exc:
        raise AuthenticationRequiredError("Invalid token subject") from exc

    try:
        user, profile = await auth_service.get_user_with_profile(user_id)
    except ValueError as exc:
        raise AuthenticationRequiredError(str(exc)) from exc

    access_token = create_access_token(user_id)
    new_refresh_token = create_refresh_token(user_id)

    response.set_cookie(
        "REFRESH_TOKEN",
        new_refresh_token,
        httponly=True,
        samesite="lax",
        path="/users/auth",
    )

    user_dto = await auth_service.build_user_dto(
        user=user,
        profile=profile,
        viewer_id=user_id,
    )
    return {
        "code": 201,
        "message": "Refresh token successfully.",
        "data": {
            "accessToken": access_token,
            "user": user_dto,
        },
    }


@router.post(
    "/auth/logout",
    summary="Logout",
)
async def user_logout(
    response: Response,
    cookie_header: Annotated[str | None, Header(alias="cookie")] = None,
) -> dict:
    # Stateless JWT flow: we simply clear the refresh token cookie.
    if cookie_header is None:
        raise AuthenticationRequiredError("Refresh token cookie is missing")

    # Clear cookie on client; access tokens will naturally expire.
    response.delete_cookie(
        "REFRESH_TOKEN",
        path="/users/auth",
    )
    return {
        "code": 201,
        "message": "Logout successfully.",
    }


@router.post(
    "/auth/sudo",
    summary="Verify credentials for privileged operations",
)
async def sudo_auth(
    payload: dict,
    auth_user: AuthUserInfo = Depends(get_auth_user),
    auth_service: UserAuthService = Depends(get_user_auth_service),
) -> dict:
    from redis.asyncio import Redis as AsyncRedis

    from app.core.config import settings
    from app.domain.user.login_security import TOTPService

    method = payload.get("method")
    credentials = payload.get("credentials", {})

    if method == "password":
        password = credentials.get("password")
        if not password:
            raise BadRequestError("password is required")

        user, profile = await auth_service.get_user_with_profile(auth_user.user_id)
        if not user.hashed_password or user.hashed_password.startswith("SRP:"):
            raise AuthenticationRequiredError(
                "Password authentication not available for this account"
            )

        import bcrypt

        if not bcrypt.checkpw(password.encode("utf-8"), user.hashed_password.encode("utf-8")):
            raise AuthenticationRequiredError("Invalid password")

        return {
            "code": 200,
            "message": "Sudo mode activated.",
            "data": {"verified": True},
        }

    elif method == "srp":
        import srptools as _srp

        client_ephemeral = credentials.get("clientPublicEphemeral")
        client_proof = credentials.get("clientProof")

        user, _profile = await auth_service.get_user_with_profile(auth_user.user_id)
        if not user.hashed_password or not user.hashed_password.startswith("SRP:"):
            raise AuthenticationRequiredError("SRP authentication not available for this account")

        parts = user.hashed_password.split(":", 2)
        if len(parts) != 3:
            raise AuthenticationRequiredError("Corrupted SRP data")
        stored_salt, stored_verifier = parts[1], parts[2]

        redis = AsyncRedis.from_url(settings.redis_url, decode_responses=True)
        try:
            srp_key = f"srp:sudo:{auth_user.user_id}"

            if not client_ephemeral and not client_proof:
                # SRP Step 1: Generate server ephemeral and store session state
                try:
                    server_ctx = _srp.SRPContext(user.username)
                    server_session = _srp.SRPServerSession(server_ctx, stored_verifier)
                except (ValueError, _srp.SRPException):
                    raise AuthenticationRequiredError("Corrupted SRP credentials") from None
                server_public = server_session.public
                server_private = server_session.private

                # Store server private key in Redis (expires in 5 minutes)
                await redis.setex(srp_key, 300, server_private)

                return {
                    "code": 200,
                    "message": "SRP initialization.",
                    "data": {
                        "serverPublicEphemeral": server_public,
                        "salt": stored_salt,
                    },
                }

            # SRP Step 2: Verify client proof
            if not client_ephemeral or not client_proof:
                raise BadRequestError("Both clientPublicEphemeral and clientProof are required")

            server_private = await redis.get(srp_key)
            if not server_private:
                raise AuthenticationRequiredError("SRP session expired, please reinitialize")
            await redis.delete(srp_key)

            try:
                server_ctx = _srp.SRPContext(user.username)
                server_session = _srp.SRPServerSession(
                    server_ctx, stored_verifier, private=server_private
                )
                server_session.process(client_ephemeral, stored_salt)
            except (ValueError, TypeError, _srp.SRPException):
                raise AuthenticationRequiredError("Invalid SRP parameters") from None

            if not server_session.verify_proof(client_proof):
                raise AuthenticationRequiredError("Invalid SRP proof")

            # key_proof is bytes; decode for JSON serialization
            proof = server_session.key_proof
            server_proof_str = proof.decode() if isinstance(proof, bytes) else str(proof)

            return {
                "code": 200,
                "message": "Sudo mode activated via SRP.",
                "data": {
                    "verified": True,
                    "serverProof": server_proof_str,
                },
            }
        finally:
            await redis.aclose()

    elif method == "totp":
        code = credentials.get("code")
        if not code:
            raise BadRequestError("code is required")

        redis = AsyncRedis.from_url(settings.redis_url, decode_responses=False)
        try:
            totp_service = TOTPService(redis)
            if not await totp_service.is_2fa_enabled(auth_user.user_id):
                raise AuthenticationRequiredError("2FA is not enabled")
            if not await totp_service.verify_2fa(auth_user.user_id, code):
                raise AuthenticationRequiredError("Invalid 2FA code")
            return {
                "code": 200,
                "message": "Sudo mode activated via 2FA.",
                "data": {"verified": True},
            }
        finally:
            await redis.aclose()

    else:
        raise BadRequestError(f"Unknown auth method: {method}")


@router.get(
    "/{userId}/identity",
    summary="Get User Real Name Identity Info",
)
async def get_user_identity(
    user_id: Annotated[int, Path(ge=1, alias="userId")],
    request: Request,
    precise: bool = Query(default=False),
    moduleType: str | None = Query(default=None),
    moduleEntityId: int | None = Query(default=None),
    accessReason: str | None = Query(default=None),
    accessType: str = Query(default="VIEW"),
    auth_user: AuthUserInfo = Depends(get_auth_user),
    realname_service: UserRealNameService = Depends(get_user_realname_service),
) -> dict:
    if precise and auth_user.user_id != user_id:
        raise ForbiddenError("Precise identity view only allowed for the owner.")

    try:
        if precise:
            identity = await realname_service.get_user_identity(user_id)
        else:
            identity = await realname_service.get_fuzzy_user_identity(user_id)

        data = {
            "hasIdentity": True,
            "identity": identity,
        }

        if precise:
            await realname_service.log_access(
                accessor_id=auth_user.user_id,
                target_id=user_id,
                access_reason=accessReason or "Precise real-name view",
                access_type=accessType,
                ip_address=request.client.host if request.client else "",
                module_type=moduleType,
                module_entity_id=moduleEntityId,
            )
    except NotFoundError:
        data = {
            "hasIdentity": False,
            "identity": None,
        }

    return {"code": 200, "message": "Success", "data": data}


@router.put(
    "/{userId}/identity",
    summary="Update User Real Name Identity Info",
)
async def put_user_identity(
    user_id: Annotated[int, Path(ge=1, alias="userId")],
    payload: dict,
    auth_user: AuthUserInfo = Depends(get_auth_user),
    realname_service: UserRealNameService = Depends(get_user_realname_service),
) -> dict:
    if auth_user.user_id != user_id:
        raise ForbiddenError("Only the user themselves can update identity.")
    identity = {
        "realName": payload.get("realName") or "",
        "studentId": payload.get("studentId") or "",
        "grade": payload.get("grade") or "",
        "major": payload.get("major") or "",
        "className": payload.get("className") or "",
    }
    stored = await realname_service.create_or_update_user_identity(
        user_id=user_id,
        real_name=identity["realName"],
        student_id=identity["studentId"],
        grade=identity["grade"],
        major=identity["major"],
        class_name=identity["className"],
    )
    return {"code": 200, "message": "Success", "data": {"identity": stored}}


@router.patch(
    "/{userId}/identity",
    summary="Update User Real Name Identity Info (partial)",
)
async def patch_user_identity(
    user_id: Annotated[int, Path(ge=1, alias="userId")],
    payload: dict,
    auth_user: AuthUserInfo = Depends(get_auth_user),
    realname_service: UserRealNameService = Depends(get_user_realname_service),
) -> dict:
    if auth_user.user_id != user_id:
        raise ForbiddenError("Only the user themselves can update identity.")
    try:
        existing = await realname_service.get_user_identity(user_id)
        base = existing.copy()
    except NotFoundError:
        base = {
            "realName": "",
            "studentId": "",
            "grade": "",
            "major": "",
            "className": "",
        }
    def _merge(key: str) -> str:
        val = payload.get(key)
        return val if val is not None else base[key]

    merged = {
        "realName": _merge("realName"),
        "studentId": _merge("studentId"),
        "grade": _merge("grade"),
        "major": _merge("major"),
        "className": _merge("className"),
    }
    stored = await realname_service.create_or_update_user_identity(
        user_id=user_id,
        real_name=merged["realName"],
        student_id=merged["studentId"],
        grade=merged["grade"],
        major=merged["major"],
        class_name=merged["className"],
    )
    return {"code": 200, "message": "Success", "data": {"identity": stored}}


@router.get(
    "/{userId}/identity/access-logs",
    summary="Get User Real Name Identity Access Logs",
)
async def get_user_identity_access_logs(
    user_id: Annotated[int, Path(ge=1, alias="userId")],
    pageStart: int | None = Query(default=None),
    pageSize: int = Query(default=20, ge=1, le=200),
    auth_user: AuthUserInfo = Depends(get_auth_user),
    realname_service: UserRealNameService = Depends(get_user_realname_service),
) -> dict:
    if auth_user.user_id != user_id:
        raise ForbiddenError("Only the user themselves can view identity access logs.")
    logs, page = await realname_service.get_access_logs(
        target_user_id=user_id,
        page_size=pageSize,
        page_start=pageStart,
    )
    data = {
        "logs": logs,
        "page": page,
    }
    return {"code": 200, "message": "Success", "data": data}


@router.post(
    "/auth/2fa/enable",
    summary="Start 2FA setup",
)
async def enable_2fa(
    auth_user: AuthUserInfo = Depends(get_auth_user),
    auth_service: UserAuthService = Depends(get_user_auth_service),
) -> dict:
    from redis.asyncio import Redis as AsyncRedis

    from app.core.config import settings
    from app.domain.user.login_security import TOTPService

    redis = AsyncRedis.from_url(settings.redis_url, decode_responses=False)
    try:
        totp_service = TOTPService(redis)

        if await totp_service.is_2fa_enabled(auth_user.user_id):
            raise BadRequestError("2FA is already enabled")

        user, profile = await auth_service.get_user_with_profile(auth_user.user_id)
        result = await totp_service.start_2fa_setup(auth_user.user_id, user.email or user.username)

        return {
            "code": 200,
            "message": "2FA setup started. Scan the QR code with your authenticator app.",
            "data": {
                "secret": result["secret"],
                "provisioningUri": result["provisioningUri"],
            },
        }
    finally:
        await redis.aclose()


@router.post(
    "/auth/2fa/verify",
    summary="Verify and complete 2FA setup",
)
async def verify_2fa_setup(
    payload: dict,
    auth_user: AuthUserInfo = Depends(get_auth_user),
) -> dict:
    from redis.asyncio import Redis as AsyncRedis

    from app.core.config import settings
    from app.domain.user.login_security import TOTPService

    code = payload.get("code")
    if not code:
        raise BadRequestError("code is required")

    redis = AsyncRedis.from_url(settings.redis_url, decode_responses=False)
    try:
        totp_service = TOTPService(redis)

        if await totp_service.is_2fa_enabled(auth_user.user_id):
            raise BadRequestError("2FA is already enabled")

        result = await totp_service.confirm_2fa_setup(auth_user.user_id, code)
        if not result:
            raise BadRequestError("Invalid or expired verification code")

        return {
            "code": 200,
            "message": "2FA enabled successfully.",
            "data": {
                "enabled": True,
            },
        }
    finally:
        await redis.aclose()


@router.delete(
    "/auth/2fa",
    summary="Disable 2FA",
)
async def disable_2fa(
    payload: dict,
    auth_user: AuthUserInfo = Depends(get_auth_user),
) -> dict:
    from redis.asyncio import Redis as AsyncRedis

    from app.core.config import settings
    from app.domain.user.login_security import TOTPService

    code = payload.get("code")
    if not code:
        raise BadRequestError("code is required to disable 2FA")

    redis = AsyncRedis.from_url(settings.redis_url, decode_responses=False)
    try:
        totp_service = TOTPService(redis)

        if not await totp_service.is_2fa_enabled(auth_user.user_id):
            raise BadRequestError("2FA is not enabled")

        if not await totp_service.verify_2fa(auth_user.user_id, code):
            raise AuthenticationRequiredError("Invalid 2FA code")

        await totp_service.disable_2fa(auth_user.user_id)

        return {
            "code": 200,
            "message": "2FA disabled successfully.",
            "data": {
                "enabled": False,
            },
        }
    finally:
        await redis.aclose()


@router.get(
    "/auth/2fa/status",
    summary="Get 2FA status",
)
async def get_2fa_status(
    auth_user: AuthUserInfo = Depends(get_auth_user),
) -> dict:
    from redis.asyncio import Redis as AsyncRedis

    from app.core.config import settings
    from app.domain.user.login_security import TOTPService

    redis = AsyncRedis.from_url(settings.redis_url, decode_responses=False)
    try:
        totp_service = TOTPService(redis)
        enabled = await totp_service.is_2fa_enabled(auth_user.user_id)

        return {
            "code": 200,
            "message": "OK",
            "data": {
                "enabled": enabled,
            },
        }
    finally:
        await redis.aclose()


@router.get(
    "/me/sessions",
    summary="List active sessions",
)
async def list_sessions(
    auth_user: AuthUserInfo = Depends(get_auth_user),
) -> dict:
    from redis.asyncio import Redis as AsyncRedis

    from app.core.config import settings
    from app.domain.user.login_security import SessionManager

    redis = AsyncRedis.from_url(settings.redis_url, decode_responses=False)
    try:
        session_manager = SessionManager(redis)
        sessions = await session_manager.list_user_sessions(auth_user.user_id)

        session_list = [
            {
                "id": s.get("session_id"),
                "deviceInfo": s.get("device_info", ""),
                "ipAddress": s.get("ip_address", ""),
                "userAgent": s.get("user_agent", ""),
                "createdAt": s.get("created_at"),
                "lastActiveAt": s.get("last_active_at"),
            }
            for s in sessions
        ]

        return {
            "code": 200,
            "message": "OK",
            "data": {
                "sessions": session_list,
            },
        }
    finally:
        await redis.aclose()


@router.delete(
    "/me/sessions/{sessionId}",
    summary="Revoke a session",
)
async def revoke_session(
    session_id: Annotated[str, Path(alias="sessionId")],
    auth_user: AuthUserInfo = Depends(get_auth_user),
) -> dict:
    from redis.asyncio import Redis as AsyncRedis

    from app.core.config import settings
    from app.domain.user.login_security import SessionManager

    redis = AsyncRedis.from_url(settings.redis_url, decode_responses=False)
    try:
        session_manager = SessionManager(redis)
        success = await session_manager.revoke_session(session_id, auth_user.user_id)

        if not success:
            raise NotFoundError("Session not found")

        return {
            "code": 200,
            "message": "Session revoked successfully.",
        }
    finally:
        await redis.aclose()


@router.delete(
    "/me/sessions",
    summary="Revoke all other sessions",
)
async def revoke_all_sessions(
    request: Request,
    auth_user: AuthUserInfo = Depends(get_auth_user),
) -> dict:
    from redis.asyncio import Redis as AsyncRedis

    from app.core.config import settings
    from app.domain.user.login_security import SessionManager

    current_session_id = request.cookies.get("SESSION_ID")

    redis = AsyncRedis.from_url(settings.redis_url, decode_responses=False)
    try:
        session_manager = SessionManager(redis)
        count = await session_manager.revoke_all_sessions(
            auth_user.user_id,
            except_session_id=current_session_id,
        )

        return {
            "code": 200,
            "message": f"Revoked {count} sessions.",
            "data": {
                "revokedCount": count,
            },
        }
    finally:
        await redis.aclose()


@router.post(
    "/password/forgot",
    summary="Request password reset",
)
async def forgot_password(
    payload: dict,
    auth_service: UserAuthService = Depends(get_user_auth_service),
) -> dict:
    from redis.asyncio import Redis as AsyncRedis

    from app.core.config import settings
    from app.core.email import get_email_sender
    from app.domain.user.login_security import PasswordResetService

    email = payload.get("email")
    if not email:
        raise BadRequestError("email is required")

    user = await auth_service.get_user_by_email(email)
    if user is None:
        return {
            "code": 200,
            "message": "If the email exists, a reset link has been sent.",
        }

    redis = AsyncRedis.from_url(settings.redis_url, decode_responses=False)
    try:
        reset_service = PasswordResetService(redis)
        token = await reset_service.create_reset_token(user.id, email)

        sender = get_email_sender()
        reset_url = f"{settings.frontend_url}/reset-password?token={token}"
        subject = "[Cheese] Password Reset Request"
        body_html = f"""
        <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
            <h2 style="color: #333;">Password Reset</h2>
            <p>You requested to reset your password. Click the link below:</p>
            <p><a href="{reset_url}" style="color: #007bff;">{reset_url}</a></p>
            <p>This link will expire in 30 minutes.</p>
            <p style="color: #666; font-size: 12px;">If you didn't request this, please ignore this email.</p>
        </div>
        """
        body_text = f"Reset your password: {reset_url}\nThis link expires in 30 minutes."
        sender.send(to=email, subject=subject, body_html=body_html, body_text=body_text)

        return {
            "code": 200,
            "message": "If the email exists, a reset link has been sent.",
        }
    finally:
        await redis.aclose()


@router.post(
    "/password/reset",
    summary="Reset password with token",
)
async def reset_password(
    payload: dict,
    auth_service: UserAuthService = Depends(get_user_auth_service),
) -> dict:
    from redis.asyncio import Redis as AsyncRedis

    from app.core.config import settings
    from app.domain.user.login_security import PasswordResetService

    token = payload.get("token")
    new_password = payload.get("password")
    if not token or not new_password:
        raise BadRequestError("token and password are required")

    if len(new_password) < 6:
        raise BadRequestError("Password must be at least 6 characters")

    redis = AsyncRedis.from_url(settings.redis_url, decode_responses=False)
    try:
        reset_service = PasswordResetService(redis)
        token_data = await reset_service.consume_reset_token(token)

        if not token_data:
            raise BadRequestError("Invalid or expired reset token")

        user_id = int(token_data["user_id"])
        await auth_service.update_password(user_id, new_password)

        return {
            "code": 200,
            "message": "Password reset successfully.",
        }
    finally:
        await redis.aclose()


@router.post(
    "/recover/password/request",
    summary="Request password recovery",
)
async def recover_password_request(
    payload: dict,
    auth_service: UserAuthService = Depends(get_user_auth_service),
) -> dict:
    import re

    from redis.asyncio import Redis as AsyncRedis

    from app.core.config import settings
    from app.core.email import get_email_sender
    from app.domain.user.login_security import PasswordResetService

    email = payload.get("email")
    if not email:
        raise BadRequestError("email is required")

    email_regex = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
    if not re.match(email_regex, email):
        raise UnprocessableEntityError("Invalid email address format")

    allowed_suffixes = (".ruc.edu.cn", ".edu.cn", ".edu")
    if not any(email.endswith(suffix) for suffix in allowed_suffixes):
        raise UnprocessableEntityError("Email must be from an educational institution")

    user = await auth_service.get_user_by_email(email)
    if user is None:
        return {
            "code": 200,
            "message": "If the email exists, a reset link has been sent.",
        }

    redis = AsyncRedis.from_url(settings.redis_url, decode_responses=False)
    try:
        reset_service = PasswordResetService(redis)
        token = await reset_service.create_reset_token(user.id, email)

        sender = get_email_sender()
        reset_url = f"{settings.frontend_url}/reset-password?token={token}"
        subject = "[Cheese] Password Reset Request"
        body_html = f"""
        <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
            <h2 style="color: #333;">Password Reset</h2>
            <p>You requested to reset your password. Click the link below:</p>
            <p><a href="{reset_url}" style="color: #007bff;">{reset_url}</a></p>
            <p>This link will expire in 30 minutes.</p>
            <p style="color: #666; font-size: 12px;">If you didn't request this, please ignore this email.</p>
        </div>
        """
        body_text = f"Reset your password: {reset_url}\nThis link expires in 30 minutes."
        sender.send(to=email, subject=subject, body_html=body_html, body_text=body_text)

        return {
            "code": 200,
            "message": "If the email exists, a reset link has been sent.",
        }
    finally:
        await redis.aclose()


@router.post(
    "/recover/password/verify",
    summary="Verify password recovery token",
)
async def recover_password_verify(
    payload: dict,
    auth_service: UserAuthService = Depends(get_user_auth_service),
) -> dict:
    from redis.asyncio import Redis as AsyncRedis

    from app.core.config import settings
    from app.domain.user.login_security import PasswordResetService

    token = payload.get("token")
    new_password = payload.get("password")
    srp_salt = payload.get("srpSalt")
    srp_verifier = payload.get("srpVerifier")

    if not token:
        raise BadRequestError("token is required")

    has_password = bool(new_password)
    has_srp = srp_salt and srp_verifier

    if not has_password and not has_srp:
        raise BadRequestError("Either password or srpSalt/srpVerifier is required")

    redis = AsyncRedis.from_url(settings.redis_url, decode_responses=False)
    try:
        reset_service = PasswordResetService(redis)
        token_data = await reset_service.consume_reset_token(token)

        if not token_data:
            raise UnprocessableEntityError("Invalid or expired reset token")

        user_id = int(token_data["user_id"])
        if has_password:
            await auth_service.update_password(user_id, new_password)
        else:
            srp_data = f"SRP:{srp_salt}:{srp_verifier}"
            await auth_service._user_repo.update_password(user_id, srp_data)

        return {
            "code": 200,
            "message": "Password reset successfully.",
        }
    finally:
        await redis.aclose()


@router.get(
    "/{userId}/favorites/questions",
    summary="List user favorite questions",
)
async def get_user_favorite_questions(
    user_id: Annotated[int, Path(ge=0, alias="userId")],
    page_start: int | None = Query(default=None, alias="page_start"),
    page_size: int = Query(default=20, ge=1, le=100, alias="page_size"),
    auth_user: AuthUserInfo = Depends(get_auth_user),
    db=Depends(get_db),
) -> dict:
    question_repo = QuestionRepository(session=db)
    topic_repo = QuestionTopicRepository(session=db)

    offset = page_start or 0
    rows, total = await question_repo.list_followed(user_id=user_id, limit=page_size, offset=offset)
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
    page_start: int | None = Query(default=None, alias="page_start"),
    page_size: int = Query(default=20, ge=1, le=100, alias="page_size"),
    auth_user: AuthUserInfo = Depends(get_auth_user),
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
    auth_user: AuthUserInfo = Depends(get_auth_user),
) -> dict:
    if auth_user.user_id != user_id:
        raise ForbiddenError("Only the user themselves can view their settings.")
    settings = {
        "emailNotification": True,
        "pushNotification": True,
        "language": "zh-CN",
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
    auth_user: AuthUserInfo = Depends(get_auth_user),
) -> dict:
    if auth_user.user_id != user_id:
        raise ForbiddenError("Only the user themselves can update their settings.")
    settings = {
        "emailNotification": payload.get("emailNotification", True),
        "pushNotification": payload.get("pushNotification", True),
        "language": payload.get("language", "zh-CN"),
        "theme": payload.get("theme", "light"),
    }
    return {"code": 200, "message": "OK", "data": {"settings": settings}}


@router.get(
    "",
    summary="List users",
)
async def list_users(
    q: str | None = Query(default=None),
    page_start: int | None = Query(default=None, alias="page_start"),
    page_size: int = Query(default=20, ge=1, le=100, alias="page_size"),
    auth_user: AuthUserInfo = Depends(get_auth_user),
    db=Depends(get_db),
) -> dict:
    """List users with optional search query."""
    profile_repo = UserProfileRepository(session=db)
    user_repo = UserRepository(session=db)
    follow_repo = UserFollowingRepository(session=db)
    stats_repo = UserStatisticsRepository(session=db)
    auth_service = UserAuthService(
        user_repo=user_repo,
        profile_repo=profile_repo,
        follow_repo=follow_repo,
        stats_repo=stats_repo,
    )

    offset = page_start or 0
    profiles = await profile_repo.list_profiles(limit=page_size, offset=offset)

    if q:
        filtered_profiles = []
        for profile in profiles:
            user = await user_repo.get_by_id(profile.user_id)
            if user and (
                q.lower() in user.username.lower() or q.lower() in profile.nickname.lower()
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


@router.post(
    "/{userId}/2fa/enable",
    summary="Start 2FA setup for user",
)
async def enable_user_2fa(
    user_id: Annotated[int, Path(ge=0, alias="userId")],
    payload: dict = Body(default={}),
    auth_user: AuthUserInfo = Depends(get_auth_user),
    auth_service: UserAuthService = Depends(get_user_auth_service),
) -> dict:
    from redis.asyncio import Redis as AsyncRedis

    from app.core.config import settings
    from app.domain.user.login_security import TOTPService

    if auth_user.user_id != user_id:
        raise ForbiddenError("Only the user themselves can enable 2FA.")

    secret = payload.get("secret")
    code = payload.get("code")

    redis = AsyncRedis.from_url(settings.redis_url, decode_responses=False)
    try:
        totp_service = TOTPService(redis)

        if secret and code:
            if await totp_service.is_2fa_enabled(auth_user.user_id):
                raise BadRequestError("2FA is already enabled")
            result = await totp_service.confirm_2fa_setup(auth_user.user_id, code)
            if not result:
                raise UnprocessableEntityError("Invalid or expired verification code")
            return {
                "code": 200,
                "message": "2FA enabled successfully.",
                "data": {"enabled": True},
            }

        if await totp_service.is_2fa_enabled(auth_user.user_id):
            raise ForbiddenError("2FA is already enabled")

        user, profile = await auth_service.get_user_with_profile(auth_user.user_id)
        result = await totp_service.start_2fa_setup(auth_user.user_id, user.email or user.username)

        return {
            "code": 200,
            "message": "2FA setup started. Scan the QR code with your authenticator app.",
            "data": {
                "secret": result["secret"],
                "provisioningUri": result["provisioningUri"],
            },
        }
    finally:
        await redis.aclose()


@router.post(
    "/{userId}/2fa/disable",
    summary="Disable 2FA for user",
)
async def disable_user_2fa(
    user_id: Annotated[int, Path(ge=0, alias="userId")],
    payload: dict = Body(default={}),
    auth_user: AuthUserInfo = Depends(get_auth_user),
) -> dict:
    from redis.asyncio import Redis as AsyncRedis

    from app.core.config import settings
    from app.domain.user.login_security import TOTPService

    if auth_user.user_id != user_id:
        raise ForbiddenError("Only the user themselves can disable 2FA.")

    code = payload.get("code")

    redis = AsyncRedis.from_url(settings.redis_url, decode_responses=False)
    try:
        totp_service = TOTPService(redis)

        if not await totp_service.is_2fa_enabled(auth_user.user_id):
            raise BadRequestError("2FA is not enabled")

        if not code:
            raise BadRequestError("2FA code is required to disable 2FA")
        if not await totp_service.verify_2fa(auth_user.user_id, code):
            raise AuthenticationRequiredError("Invalid 2FA code")

        await totp_service.disable_2fa(auth_user.user_id)

        return {
            "code": 200,
            "message": "2FA disabled successfully.",
            "data": {"enabled": False},
        }
    finally:
        await redis.aclose()


@router.get(
    "/{userId}/2fa/status",
    summary="Get 2FA status for user",
)
async def get_user_2fa_status(
    user_id: Annotated[int, Path(ge=0, alias="userId")],
    auth_user: AuthUserInfo = Depends(get_auth_user),
) -> dict:
    from redis.asyncio import Redis as AsyncRedis

    from app.core.config import settings
    from app.domain.user.login_security import TOTPService

    if auth_user.user_id != user_id:
        raise ForbiddenError("Only the user themselves can view 2FA status.")

    redis = AsyncRedis.from_url(settings.redis_url, decode_responses=False)
    try:
        totp_service = TOTPService(redis)
        enabled = await totp_service.is_2fa_enabled(user_id)

        return {
            "code": 200,
            "message": "OK",
            "data": {"enabled": enabled},
        }
    finally:
        await redis.aclose()


@router.post(
    "/auth/passkey/register/challenge",
    summary="Generate passkey registration challenge",
)
async def passkey_register_challenge(
    auth_user: AuthUserInfo = Depends(get_auth_user),
    auth_service: UserAuthService = Depends(get_user_auth_service),
    passkey_service: PasskeyService = Depends(get_passkey_service),
) -> dict:
    import json

    from redis.asyncio import Redis as AsyncRedis

    from app.core.config import settings

    user, profile = await auth_service.get_user_with_profile(auth_user.user_id)

    options = await passkey_service.generate_registration_options(
        user_id=auth_user.user_id,
        username=user.username,
        display_name=profile.nickname if profile else user.username,
    )

    redis = AsyncRedis.from_url(settings.redis_url, decode_responses=False)
    try:
        challenge_key = f"passkey:challenge:{auth_user.user_id}:{options['challenge']}"
        await redis.set(challenge_key, json.dumps({"userId": auth_user.user_id}), ex=300)
    finally:
        await redis.aclose()

    return {
        "code": 200,
        "message": "OK",
        "data": {"options": options},
    }


@router.post(
    "/auth/passkey/register/verify",
    summary="Verify passkey registration",
)
async def passkey_register_verify(
    payload: dict,
    auth_user: AuthUserInfo = Depends(get_auth_user),
    passkey_service: PasskeyService = Depends(get_passkey_service),
) -> dict:
    from redis.asyncio import Redis as AsyncRedis

    from app.core.config import settings

    challenge = payload.get("challenge")
    credential = payload.get("credential")

    if not challenge or not credential:
        raise BadRequestError("challenge and credential are required")

    redis = AsyncRedis.from_url(settings.redis_url, decode_responses=False)
    try:
        challenge_key = f"passkey:challenge:{auth_user.user_id}:{challenge}"
        stored = await redis.get(challenge_key)
        if not stored:
            raise BadRequestError("Invalid or expired challenge")
        await redis.delete(challenge_key)
    finally:
        await redis.aclose()

    result = await passkey_service.verify_registration(
        user_id=auth_user.user_id,
        challenge=challenge,
        credential=credential,
    )

    return {
        "code": 201,
        "message": "Passkey registered successfully.",
        "data": {"passkey": result},
    }


@router.post(
    "/auth/passkey/authenticate/challenge",
    summary="Generate passkey authentication challenge",
)
async def passkey_authenticate_challenge(
    payload: dict = Body(default={}),
    passkey_service: PasskeyService = Depends(get_passkey_service),
) -> dict:
    import json

    from redis.asyncio import Redis as AsyncRedis

    from app.core.config import settings

    user_id = payload.get("userId")

    options = await passkey_service.generate_authentication_options(
        user_id=user_id,
    )

    redis = AsyncRedis.from_url(settings.redis_url, decode_responses=False)
    try:
        challenge_key = f"passkey:auth_challenge:{options['challenge']}"
        data = {"userId": user_id} if user_id else {}
        await redis.set(challenge_key, json.dumps(data), ex=300)
    finally:
        await redis.aclose()

    return {
        "code": 200,
        "message": "OK",
        "data": {"options": options},
    }


@router.post(
    "/auth/passkey/authenticate/verify",
    summary="Verify passkey authentication",
)
async def passkey_authenticate_verify(
    payload: dict,
    response: Response,
    passkey_service: PasskeyService = Depends(get_passkey_service),
    auth_service: UserAuthService = Depends(get_user_auth_service),
) -> dict:
    from redis.asyncio import Redis as AsyncRedis

    from app.core.config import settings
    from app.domain.user.login_security import SessionManager

    challenge = payload.get("challenge")
    credential = payload.get("credential")

    if not challenge or not credential:
        raise BadRequestError("challenge and credential are required")

    redis = AsyncRedis.from_url(settings.redis_url, decode_responses=False)
    try:
        challenge_key = f"passkey:auth_challenge:{challenge}"
        stored = await redis.get(challenge_key)
        if not stored:
            raise BadRequestError("Invalid or expired challenge")
        await redis.delete(challenge_key)
    finally:
        await redis.aclose()

    user_id = await passkey_service.verify_authentication(
        challenge=challenge,
        credential=credential,
    )

    user, profile = await auth_service.get_user_with_profile(user_id)

    redis = AsyncRedis.from_url(settings.redis_url, decode_responses=False)
    try:
        session_manager = SessionManager(redis)
        session_id = await session_manager.create_session(
            user_id=user_id,
            ip_address="",
            user_agent="passkey",
        )
    finally:
        await redis.aclose()

    access_token = create_access_token(user_id)
    refresh_token = create_refresh_token(user_id)

    response.set_cookie(
        "REFRESH_TOKEN",
        refresh_token,
        httponly=True,
        samesite="lax",
        path="/users/auth",
    )
    response.set_cookie(
        "SESSION_ID",
        session_id,
        httponly=True,
        samesite="lax",
        path="/",
    )

    user_dto = await auth_service.build_user_dto(
        user=user,
        profile=profile,
        viewer_id=user_id,
    )
    return {
        "code": 201,
        "message": "Login successfully.",
        "data": {
            "user": user_dto,
            "accessToken": access_token,
            "sessionId": session_id,
        },
    }


@router.get(
    "/{userId}/passkeys",
    summary="List user passkeys",
)
async def list_passkeys(
    user_id: Annotated[int, Path(ge=0, alias="userId")],
    auth_user: AuthUserInfo = Depends(get_auth_user),
    passkey_service: PasskeyService = Depends(get_passkey_service),
) -> dict:
    if auth_user.user_id != user_id:
        raise ForbiddenError("Only the user themselves can view their passkeys.")

    passkeys = await passkey_service.list_passkeys(user_id)

    return {
        "code": 200,
        "message": "OK",
        "data": {"passkeys": passkeys},
    }


@router.delete(
    "/{userId}/passkeys/{credentialId}",
    summary="Delete a passkey",
)
async def delete_passkey(
    user_id: Annotated[int, Path(ge=0, alias="userId")],
    credential_id: Annotated[str, Path(alias="credentialId")],
    auth_user: AuthUserInfo = Depends(get_auth_user),
    passkey_service: PasskeyService = Depends(get_passkey_service),
) -> dict:
    if auth_user.user_id != user_id:
        raise ForbiddenError("Only the user themselves can delete their passkeys.")

    deleted = await passkey_service.delete_passkey(user_id, credential_id)

    if not deleted:
        raise NotFoundError("Passkey not found")

    return {
        "code": 200,
        "message": "Passkey deleted successfully.",
    }


@router.get(
    "/auth/oauth/providers",
    summary="List available OAuth providers",
)
async def get_oauth_providers(
    oauth_service: OAuthService = Depends(get_oauth_service),
) -> dict:
    providers = oauth_service.get_providers_config()
    return {
        "code": 200,
        "message": "OK",
        "data": {"providers": providers},
    }


@router.get(
    "/auth/oauth/login/{providerId}",
    summary="Get OAuth authorization URL",
)
async def get_oauth_login_url(
    provider_id: Annotated[str, Path(alias="providerId")],
    redirect: str | None = Query(default=None),
    oauth_service: OAuthService = Depends(get_oauth_service),
) -> dict:
    import secrets

    state = secrets.token_urlsafe(32)

    if redirect:
        await oauth_service.store_oauth_state(state, {"redirect": redirect})

    try:
        auth_url = oauth_service.generate_authorization_url(provider_id, state)
    except NotFoundError:
        raise NotFoundError(f"OAuth provider '{provider_id}' not found or not enabled") from None

    return {
        "code": 200,
        "message": "OK",
        "data": {
            "authorizationUrl": auth_url,
            "state": state,
        },
    }


@router.get(
    "/auth/oauth/callback/{providerId}",
    summary="Handle OAuth callback",
)
async def handle_oauth_callback(
    provider_id: Annotated[str, Path(alias="providerId")],
    code: str = Query(...),
    state: str | None = Query(default=None),
    response: Response = None,
    oauth_service: OAuthService = Depends(get_oauth_service),
    auth_service: UserAuthService = Depends(get_user_auth_service),
) -> dict:
    state_data = (await oauth_service.get_oauth_state(state)) if state else None
    redirect_url = state_data.get("redirect") if state_data else None

    try:
        access_token, user_info = await oauth_service.handle_callback(
            provider_id=provider_id,
            code=code,
            state=state,
        )
    except Exception as e:
        raise BadRequestError(f"OAuth authentication failed: {e!s}") from e

    existing = await oauth_service.get_connection_by_provider(
        provider_id=provider_id,
        provider_user_id=user_info.id,
    )

    if existing:
        user_id = existing["userId"]
        user, profile = await auth_service.get_user_with_profile(user_id)

        access_token_jwt = create_access_token(user_id)
        refresh_token = create_refresh_token(user_id)

        response.set_cookie(
            "REFRESH_TOKEN",
            refresh_token,
            httponly=True,
            samesite="lax",
            path="/users/auth",
        )

        user_dto = await auth_service.build_user_dto(
            user=user,
            profile=profile,
            viewer_id=user_id,
        )
        return {
            "code": 200,
            "message": "Login successfully.",
            "data": {
                "user": user_dto,
                "accessToken": access_token_jwt,
                "isNewUser": False,
                "redirectUrl": redirect_url,
            },
        }
    else:
        return {
            "code": 200,
            "message": "OAuth user info retrieved. Link to existing account or register.",
            "data": {
                "userInfo": {
                    "providerId": provider_id,
                    "providerUserId": user_info.id,
                    "email": user_info.email,
                    "name": user_info.name,
                    "username": user_info.username,
                },
                "isNewUser": True,
                "redirectUrl": redirect_url,
            },
        }


@router.post(
    "/auth/oauth/link",
    summary="Link OAuth account to existing user",
)
async def link_oauth_account(
    payload: dict,
    auth_user: AuthUserInfo = Depends(get_auth_user),
    oauth_service: OAuthService = Depends(get_oauth_service),
) -> dict:
    provider_id = payload.get("providerId")
    provider_user_id = payload.get("providerUserId")
    raw_profile = payload.get("profile")

    if not provider_id or not provider_user_id:
        raise BadRequestError("providerId and providerUserId are required")

    existing = await oauth_service.get_connection_by_provider(
        provider_id=provider_id,
        provider_user_id=provider_user_id,
    )
    if existing:
        raise BadRequestError("This OAuth account is already linked to another user")

    connection = await oauth_service.create_connection(
        user_id=auth_user.user_id,
        provider_id=provider_id,
        provider_user_id=provider_user_id,
        raw_profile=raw_profile,
    )

    return {
        "code": 201,
        "message": "OAuth account linked successfully.",
        "data": {"connection": connection},
    }


# ── Invite Code Management ──────────────────────────────────────────────


@router.get(
    "/invite-codes",
    summary="List invite codes (admin)",
)
async def list_invite_codes(
    auth_user: AuthUserInfo = Depends(get_auth_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    from app.domain.invite.services import InviteCodeService

    service = InviteCodeService(session)
    codes = await service.list_codes()
    return {
        "code": 200,
        "message": "Success",
        "data": {
            "codes": [
                {
                    "id": c.id,
                    "code": c.code,
                    "maxUses": c.max_uses,
                    "useCount": c.use_count,
                    "isActive": c.is_active,
                    "createdBy": c.created_by,
                    "note": c.note,
                    "createdAt": c.created_at.isoformat() if c.created_at else None,
                    "expiresAt": c.expires_at.isoformat() if c.expires_at else None,
                }
                for c in codes
            ]
        },
    }


@router.post(
    "/invite-codes",
    summary="Create invite code (admin)",
)
async def create_invite_code(
    payload: dict,
    auth_user: AuthUserInfo = Depends(get_auth_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    from app.domain.invite.services import InviteCodeService

    service = InviteCodeService(session)
    invite = await service.create_code(
        max_uses=payload.get("maxUses", 1),
        created_by=auth_user.user_id,
        note=payload.get("note"),
    )
    await session.commit()
    return {
        "code": 201,
        "message": "Invite code created.",
        "data": {
            "code": invite.code,
            "id": invite.id,
            "maxUses": invite.max_uses,
        },
    }


@router.delete(
    "/invite-codes/{code_id}",
    summary="Deactivate invite code (admin)",
)
async def deactivate_invite_code(
    code_id: int,
    auth_user: AuthUserInfo = Depends(get_auth_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    from app.domain.invite.services import InviteCodeService

    service = InviteCodeService(session)
    await service.deactivate_code(code_id)
    await session.commit()
    return {"code": 200, "message": "Invite code deactivated."}


@router.get(
    "/{userId}/oauth-connections",
    summary="List user OAuth connections",
)
async def list_oauth_connections(
    user_id: Annotated[int, Path(ge=0, alias="userId")],
    auth_user: AuthUserInfo = Depends(get_auth_user),
    oauth_service: OAuthService = Depends(get_oauth_service),
) -> dict:
    if auth_user.user_id != user_id:
        raise ForbiddenError("Only the user themselves can view their OAuth connections.")

    connections = await oauth_service.list_user_connections(user_id)

    return {
        "code": 200,
        "message": "OK",
        "data": {"connections": connections},
    }


@router.delete(
    "/{userId}/oauth-connections/{connectionId}",
    summary="Unbind OAuth connection",
)
async def delete_oauth_connection(
    user_id: Annotated[int, Path(ge=0, alias="userId")],
    connection_id: Annotated[int, Path(alias="connectionId")],
    auth_user: AuthUserInfo = Depends(get_auth_user),
    oauth_service: OAuthService = Depends(get_oauth_service),
) -> dict:
    if auth_user.user_id != user_id:
        raise ForbiddenError("Only the user themselves can unbind their OAuth connections.")

    deleted = await oauth_service.delete_connection(connection_id, user_id)

    if not deleted:
        raise NotFoundError("OAuth connection not found")

    return {
        "code": 200,
        "message": "OAuth connection removed successfully.",
    }
