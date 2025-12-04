from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query, Request, Response, status
from sqlalchemy import select

from app.auth.checker import get_auth_user
from app.auth.core import AuthUserInfo
from app.common.auth import (
    create_access_token,
    create_refresh_token,
    decode_token,
)
from app.core.errors import AuthenticationRequiredError, BadRequestError, NotFoundError, ForbiddenError
from app.db.session import get_db
from app.domain.team.membership_services import TeamMembershipService
from app.domain.team.repositories import TeamMembershipApplicationRepository, TeamRepository
from app.domain.user.models import UserFollowingRelationship
from app.domain.user.repositories import (
    UserFollowingRepository,
    UserProfileRepository,
    UserRepository,
    UserRealNameRepository,
    UserStatisticsRepository,
)
from app.domain.user.realname_services import UserRealNameService
from app.domain.user.services import UserAuthService


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


@router.post(
    "/{userId}/followers",
    summary="Follow user",
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
        raise BadRequestError("Cannot follow yourself")

    user_repo = UserRepository(session=db)
    follow_repo = UserFollowingRepository(session=db)

    # 确保被关注用户存在
    target = await user_repo.get_by_id(user_id)
    if target is None:
        raise NotFoundError("User not found")

    # 避免重复关注
    if await follow_repo.is_following(auth_user.user_id, user_id):
        raise BadRequestError("User already followed")

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
    await membership_service.cancel_my_join_request(user_id=auth_user.user_id, request_id=request_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "/me/teams/{teamId}",
    summary="Leave Team",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def leave_team(
    team_id: Annotated[int, Path(ge=1, alias="teamId")],
    auth_user: AuthUserInfo = Depends(get_auth_user),
) -> None:
    _ = (team_id, auth_user)
    return None


@router.post(
    "/me/team-invitations/{invitationId}/accept",
    summary="Accept a team invitation",
)
async def accept_team_invitation(
    invitation_id: Annotated[int, Path(ge=1, alias="invitationId")],
    auth_user: AuthUserInfo = Depends(get_auth_user),
    membership_service: TeamMembershipService = Depends(get_team_membership_service),
) -> Response:
    await membership_service.accept_team_invitation(user_id=auth_user.user_id, invitation_id=invitation_id)
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
    await membership_service.decline_team_invitation(user_id=auth_user.user_id, invitation_id=invitation_id)
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
        raise BadRequestError("Cannot unfollow yourself")

    user_repo = UserRepository(session=db)
    follow_repo = UserFollowingRepository(session=db)

    # 确保目标用户存在
    target = await user_repo.get_by_id(user_id)
    if target is None:
        raise NotFoundError("User not found")

    removed = await follow_repo.soft_delete_follow(auth_user.user_id, user_id)
    if not removed:
        raise BadRequestError("User not followed yet")

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
    pageStart: int | None = Query(default=None),
    pageSize: int = Query(default=20, ge=1, le=200),
    auth_user: AuthUserInfo = Depends(get_auth_user),
    db=Depends(get_db),
) -> dict:
    """Return followers of the given user (shape only, simplified pagination)."""
    if pageSize <= 0:
        pageSize = 20

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

    # Verify target user exists
    target = await user_repo.get_by_id(user_id)
    if target is None:
        raise NotFoundError("User not found")

    # Simple offset-based pagination for now
    offset = pageStart or 0
    rel_stmt = (
        select(UserFollowingRelationship)
        .where(
            UserFollowingRelationship.followee_id == user_id,
            UserFollowingRelationship.deleted_at.is_(None),
        )
        .order_by(UserFollowingRelationship.created_at.desc())
        .limit(pageSize)
        .offset(offset)
    )
    result = await db.execute(rel_stmt)
    relations = list(result.scalars().all())

    follower_ids = [r.follower_id for r in relations]
    followers: list[dict] = []
    for fid in follower_ids:
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

    # total count for pagination metadata
    total = await follow_repo.count_followers(user_id)
    returned = len(followers)
    has_more = offset + returned < total
    next_start = offset + returned if has_more and returned > 0 else None

    page = {
        "pageStart": pageStart or 0,
        "pageSize": returned,
        "hasMore": has_more,
        "nextStart": next_start,
        "total": total,
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
    pageStart: int | None = Query(default=None),
    pageSize: int = Query(default=20, ge=1, le=200),
    auth_user: AuthUserInfo = Depends(get_auth_user),
    db=Depends(get_db),
) -> dict:
    """Return users that the given user is following."""
    if pageSize <= 0:
        pageSize = 20

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

    offset = pageStart or 0
    rel_stmt = (
        select(UserFollowingRelationship)
        .where(
            UserFollowingRelationship.follower_id == user_id,
            UserFollowingRelationship.deleted_at.is_(None),
        )
        .order_by(UserFollowingRelationship.created_at.desc())
        .limit(pageSize)
        .offset(offset)
    )
    result = await db.execute(rel_stmt)
    relations = list(result.scalars().all())

    followee_ids = [r.followee_id for r in relations]
    followees: list[dict] = []
    for fid in followee_ids:
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

    total = await follow_repo.count_following(user_id)
    returned = len(followees)
    has_more = offset + returned < total
    next_start = offset + returned if has_more and returned > 0 else None

    page = {
        "pageStart": pageStart or 0,
        "pageSize": returned,
        "hasMore": has_more,
        "nextStart": next_start,
        "total": total,
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
    "/{userId}/questions",
    summary="List questions asked by user",
)
async def get_user_questions(
    user_id: Annotated[int, Path(ge=1, alias="userId")],
    page_start: int | None = Query(default=None, alias="page_start"),
    page_size: int = Query(default=20, ge=1, le=100, alias="page_size"),
    auth_user: AuthUserInfo = Depends(get_auth_user),
) -> dict:
    """Skeleton implementation: returns empty asked-questions list with page metadata.

    NOTE: 后续会接入真实 questions ORM/service；当前仅保证响应结构与分页字段。
    """
    _ = (user_id, viewer_id)
    questions: list[dict] = []
    page = {
        "pageStart": page_start or 0,
        "pageSize": page_size,
        "hasMore": False,
        "nextStart": None,
        "total": 0,
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
    user_id: Annotated[int, Path(ge=1, alias="userId")],
    page_start: int | None = Query(default=None, alias="page_start"),
    page_size: int = Query(default=20, ge=1, le=100, alias="page_size"),
    auth_user: AuthUserInfo = Depends(get_auth_user),
) -> dict:
    """Skeleton implementation: returns empty answered-answers list with page metadata."""
    _ = (user_id, viewer_id)
    answers: list[dict] = []
    page = {
        "pageStart": page_start or 0,
        "pageSize": page_size,
        "hasMore": False,
        "nextStart": None,
        "total": 0,
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
async def send_register_email_code(payload: dict) -> dict:
    """Send email verification code for registration.

    Uses Redis for code storage (10 min TTL) and sends via configured SMTP.
    Falls back to success response if email not configured (for dev).
    """
    from redis.asyncio import Redis as AsyncRedis
    from app.core.config import settings
    from app.domain.user.verification_service import EmailVerificationService

    email = payload.get("email")
    if not email:
        raise BadRequestError("email is required")

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


@router.post(
    "",
    summary="Register User (password-based)",
)
async def register_user(
    payload: dict,
    response: Response,
    auth_service: UserAuthService = Depends(get_user_auth_service),
) -> dict:
    """Registration flow with email verification.

    支持字段：
    - username, nickname, email, emailCode, password
    - srpSalt/srpVerifier/isLegacyAuth 暂不在 Python 端使用，仅保留形状兼容性。
    """
    from redis.asyncio import Redis as AsyncRedis
    from app.core.config import settings
    from app.domain.user.verification_service import EmailVerificationService

    username = payload.get("username")
    nickname = payload.get("nickname")
    email = payload.get("email")
    email_code = payload.get("emailCode")
    password = payload.get("password")

    if not username or not nickname or not email or not email_code:
        raise BadRequestError("username, nickname, email and emailCode are required")
    if not password:
        raise BadRequestError("password is required")

    redis = AsyncRedis.from_url(settings.redis_url, decode_responses=False)
    try:
        service = EmailVerificationService(redis)
        is_valid = await service.verify_code(email, email_code)
        if not is_valid:
            raise BadRequestError("Invalid or expired verification code")
    finally:
        await redis.aclose()

    try:
        user, profile = await auth_service.register_with_password(
            username=username,
            nickname=nickname,
            email=email,
            password=password,
        )
    except ValueError as exc:
        msg = str(exc)
        if msg == "USERNAME_TAKEN":
            raise BadRequestError("Username already registered") from exc
        if msg == "EMAIL_TAKEN":
            raise BadRequestError("Email already registered") from exc
        raise

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
        raise NotFoundError("User not found")

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
    user_id: Annotated[int, Path(ge=1, alias="userId")],
    auth_user: AuthUserInfo = Depends(get_auth_user),
    auth_service: UserAuthService = Depends(get_user_auth_service),
) -> dict:
    """Return the public profile of a user."""
    try:
        user, profile = await auth_service.get_user_with_profile(user_id)
    except ValueError:
        raise NotFoundError("User not found")

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
    from app.domain.user.login_security import LoginRateLimiter, TOTPService, SessionManager

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
            raise AuthenticationRequiredError(f"Invalid username or password. {remaining_attempts} attempts remaining")

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
    merged = {
        "realName": payload.get("realName") or base["realName"],
        "studentId": payload.get("studentId") or base["studentId"],
        "grade": payload.get("grade") or base["grade"],
        "major": payload.get("major") or base["major"],
        "className": payload.get("className") or base["className"],
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
        result = await totp_service.start_2fa_setup(auth_user.user_id, profile.email or user.username)

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
        reset_url = f"{settings.legacy_url}/reset-password?token={token}"
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
