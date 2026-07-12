import asyncio
import logging
from typing import Annotated

from fastapi import APIRouter, Body, Depends, Path, Query, Request, Response, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.checker import require_auth_user
from app.auth.core import AuthUserInfo
from app.common.auth import (
    create_access_token,
    create_refresh_token,
    decode_token,
)

# srp re-exports from the compiled Rust extension srp_rs, which pyright can't
# introspect; the symbols exist at runtime (see app/common/srp.py).
from app.common.srp import (
    generate_server_ephemeral as _srp_generate_ephemeral,  # type: ignore[attr-defined]
)
from app.common.srp import (
    verify_session as _srp_verify_session,  # type: ignore[attr-defined]
)
from app.core.config import settings
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
from app.domain.questions.repositories import (
    QuestionRepository,
    QuestionTopicRepository,
)
from app.domain.team.membership_services import TeamMembershipService
from app.domain.team.repositories import (
    TeamMembershipApplicationRepository,
    TeamRepository,
)
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

# ── Request Models ────────────────────────────────────────────────────────────


class SendEmailCodeRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    email: str = Field(..., min_length=1)
    invite_code: str | None = Field(default=None, alias="inviteCode")


class RegisterUserRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    username: str = Field(..., min_length=1)
    nickname: str = Field(..., min_length=1)
    email: str = Field(..., min_length=1)
    email_code: str = Field(..., alias="emailCode", min_length=1)
    password: str | None = None
    srp_salt: str | None = Field(default=None, alias="srpSalt")
    srp_verifier: str | None = Field(default=None, alias="srpVerifier")
    invite_code: str | None = Field(default=None, alias="inviteCode")
    is_legacy_auth: bool = Field(default=False, alias="isLegacyAuth")


class LoginRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    username: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)
    totp_code: str | None = Field(default=None, alias="totpCode")


class SrpInitRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    username: str = Field(..., min_length=1)


class SrpVerifyRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    username: str = Field(..., min_length=1)
    client_public_ephemeral: str = Field(..., alias="clientPublicEphemeral")
    client_proof: str = Field(..., alias="clientProof")
    totp_code: str | None = Field(default=None, alias="totpCode")


class SudoAuthRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    method: str
    credentials: dict = Field(default_factory=dict)


class PutUserIdentityRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    real_name: str = Field(default="", alias="realName")
    student_id: str = Field(default="", alias="studentId")
    grade: str = ""
    major: str = ""
    class_name: str = Field(default="", alias="className")


class PatchUserIdentityRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    real_name: str | None = Field(default=None, alias="realName")
    student_id: str | None = Field(default=None, alias="studentId")
    grade: str | None = None
    major: str | None = None
    class_name: str | None = Field(default=None, alias="className")


class TwoFactorCodeRequest(BaseModel):
    code: str = Field(..., min_length=1)


class ForgotPasswordRequest(BaseModel):
    email: str = Field(..., min_length=1)


class ResetPasswordRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    token: str = Field(..., min_length=1)
    password: str | None = None
    srp_salt: str | None = Field(default=None, alias="srpSalt")
    srp_verifier: str | None = Field(default=None, alias="srpVerifier")


class LinkOAuthRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    provider_id: str = Field(..., alias="providerId", min_length=1)
    provider_user_id: str = Field(..., alias="providerUserId", min_length=1)
    profile: dict | None = None


class CreateInviteCodeRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    max_uses: int = Field(default=1, alias="maxUses")
    note: str | None = None


router = APIRouter(prefix="/users", tags=["Users"])

logger = logging.getLogger(__name__)


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
    return TeamMembershipService(
        session=db, team_repo=team_repo, application_repo=app_repo
    )


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
    auth_user: AuthUserInfo = Depends(require_auth_user),
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
    auth_user: AuthUserInfo = Depends(require_auth_user),
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
    auth_user: AuthUserInfo = Depends(require_auth_user),
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
    auth_user: AuthUserInfo = Depends(require_auth_user),
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
    auth_user: AuthUserInfo = Depends(require_auth_user),
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
    auth_user: AuthUserInfo = Depends(require_auth_user),
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
    auth_user: AuthUserInfo = Depends(require_auth_user),
    membership_service: TeamMembershipService = Depends(get_team_membership_service),
    db=Depends(get_db),
) -> dict:
    from app.api.routes.teams import _application_to_api_model, _load_application_maps

    status_enum = None
    if status is not None:
        upper = status.upper()
        if upper in {
            "PENDING",
            "APPROVED",
            "REJECTED",
            "ACCEPTED",
            "DECLINED",
            "CANCELED",
        }:
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
    users_map, profiles_map, teams_map = await _load_application_maps(db, apps)
    items = [
        _application_to_api_model(
            app, users_map=users_map, profiles_map=profiles_map, teams_map=teams_map
        )
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
    auth_user: AuthUserInfo = Depends(require_auth_user),
    membership_service: TeamMembershipService = Depends(get_team_membership_service),
    db=Depends(get_db),
) -> dict:
    from app.api.routes.teams import _application_to_api_model, _load_application_maps

    status_enum = None
    if status is not None:
        upper = status.upper()
        if upper in {
            "PENDING",
            "APPROVED",
            "REJECTED",
            "ACCEPTED",
            "DECLINED",
            "CANCELED",
        }:
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
    users_map, profiles_map, teams_map = await _load_application_maps(db, apps)
    items = [
        _application_to_api_model(
            app, users_map=users_map, profiles_map=profiles_map, teams_map=teams_map
        )
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
    auth_user: AuthUserInfo = Depends(require_auth_user),
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
    page_start: int | None = Query(default=None, alias="pageStart"),
    page_size: int = Query(default=20, ge=1, le=200, alias="pageSize"),
    auth_user: AuthUserInfo = Depends(require_auth_user),
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
        "pageStart": first_id,
        "pageSize": returned,
        "hasPrev": has_prev,
        "prevStart": prev_start,
        "hasMore": has_more,
        "nextStart": next_start,
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
    page_start: int | None = Query(default=None, alias="pageStart"),
    page_size: int = Query(default=20, ge=1, le=200, alias="pageSize"),
    auth_user: AuthUserInfo = Depends(require_auth_user),
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
        "pageStart": first_id,
        "pageSize": returned,
        "hasPrev": has_prev,
        "prevStart": prev_start,
        "hasMore": has_more,
        "nextStart": next_start,
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
    page_start: int | None = Query(default=None, alias="pageStart"),
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
    page_start: int | None = Query(default=None, alias="pageStart"),
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
    page_start: int | None = Query(default=None, alias="pageStart"),
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
    payload: SendEmailCodeRequest,
    db=Depends(get_db),
) -> dict:
    """Send email verification code for registration.

    Uses Redis for code storage (10 min TTL) and sends via configured SMTP.
    Falls back to success response if email not configured (for dev).
    Any valid email address is accepted.
    """
    import re

    from redis.asyncio import Redis as AsyncRedis

    from app.core.config import settings
    from app.core.errors import ConflictError
    from app.domain.user.verification_service import EmailVerificationService

    email = payload.email

    email_regex = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
    if not re.match(email_regex, email):
        raise UnprocessableEntityError("Invalid email address format")

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
        },
    }


@router.post(
    "",
    summary="Register User",
)
async def register_user(
    payload: RegisterUserRequest,
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

    username = payload.username
    nickname = payload.nickname
    email = payload.email
    email_code = payload.email_code
    password = payload.password
    srp_salt = payload.srp_salt
    srp_verifier = payload.srp_verifier
    invite_code = payload.invite_code

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
            raise UnprocessableEntityError(
                error_map.get(msg, "Invalid invite code")
            ) from exc

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
        password_pattern = (
            r'^(?=.*[a-zA-Z])(?=.*[!@#$%^&*()_+\-=\[\]{};\':"\\|,.<>\/?]).{8,}$'
        )
        if not re.match(password_pattern, password):
            raise UnprocessableEntityError(
                "Password must be at least 8 characters and contain letters and special characters"  # noqa: E501
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
            if srp_salt is None or srp_verifier is None:
                raise BadRequestError("srpSalt and srpVerifier are required")
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

    access_token = create_access_token(user.id, handle=user.username)
    refresh_token = create_refresh_token(user.id)

    response.set_cookie(
        "REFRESH_TOKEN",
        refresh_token,
        httponly=True,
        secure=settings.environment not in ("development", "test"),
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
    auth_user: AuthUserInfo = Depends(require_auth_user),
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
    auth_user: AuthUserInfo = Depends(require_auth_user),
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
    auth_user: AuthUserInfo = Depends(require_auth_user),
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
    auth_user: AuthUserInfo = Depends(require_auth_user),
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
    description="Returns which auth methods a user supports. Returns safe defaults for non-existent users.",  # noqa: E501
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
        select(func.count())
        .select_from(PasskeyCredential)
        .where(PasskeyCredential.user_id == user.id)
    )
    passkey_count = passkey_result.scalar() or 0

    # Check 2FA
    redis = AsyncRedis.from_url(settings.redis_url, decode_responses=False)
    try:
        totp_service = TOTPService(redis)
        has_2fa = await totp_service.is_2fa_enabled(user.id)
    finally:
        await redis.aclose()

    supports_srp = bool(
        user.hashed_password and user.hashed_password.startswith("SRP:")
    )

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
    payload: LoginRequest,
    request: Request,
    response: Response,
    auth_service: UserAuthService = Depends(get_user_auth_service),
) -> dict:
    from redis.asyncio import Redis as AsyncRedis

    from app.core.config import settings
    from app.domain.user.login_security import (
        LoginRateLimiter,
        SessionManager,
        TOTPService,
    )

    username = payload.username
    password = payload.password
    totp_code = payload.totp_code

    redis = AsyncRedis.from_url(settings.redis_url, decode_responses=False)
    try:
        rate_limiter = LoginRateLimiter(redis)

        if await rate_limiter.is_locked_out(username):
            remaining = await rate_limiter.get_remaining_lockout_seconds(username)
            raise ForbiddenError(f"Account locked. Try again in {remaining} seconds")

        auth_result = await auth_service.authenticate(
            username=username, password=password
        )
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
                from app.common.auth import create_2fa_pending_token

                # tempToken lets the client finish via POST /auth/verify-2fa
                # (same contract as the SRP path).
                return {
                    "code": 200,
                    "message": "2FA required",
                    "data": {
                        "requires2FA": True,
                        "userId": user.id,
                        "tempToken": create_2fa_pending_token(user.id),
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

        access_token = create_access_token(user.id, handle=user.username)
        refresh_token = create_refresh_token(user.id)

        response.set_cookie(
            "REFRESH_TOKEN",
            refresh_token,
            httponly=True,
            secure=settings.environment not in ("development", "test"),
            samesite="lax",
            path="/",
        )
        response.set_cookie(
            "SESSION_ID",
            session_id,
            httponly=True,
            secure=settings.environment not in ("development", "test"),
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
    payload: SrpInitRequest,
    auth_service: UserAuthService = Depends(get_user_auth_service),
) -> dict:
    """SRP login step 1: return server ephemeral and salt."""
    from redis.asyncio import Redis as AsyncRedis

    from app.core.config import settings

    username = payload.username

    user = await auth_service._user_repo.get_by_username(username)
    if (
        user is None
        or not user.hashed_password
        or not user.hashed_password.startswith("SRP:")
    ):
        raise AuthenticationRequiredError("Invalid username or password")

    parts = user.hashed_password.split(":", 2)
    if len(parts) != 3:
        raise AuthenticationRequiredError("Invalid username or password")
    stored_salt, stored_verifier = parts[1], parts[2]

    server_public, server_secret = _srp_generate_ephemeral(stored_verifier)

    redis = AsyncRedis.from_url(settings.redis_url, decode_responses=True)
    try:
        srp_key = f"srp:login:{username}"
        await redis.setex(srp_key, 300, server_secret)
    finally:
        await redis.aclose()

    return {
        "code": 200,
        "message": "SRP initialization.",
        "data": {
            "serverPublicEphemeral": server_public,
            "salt": stored_salt,
        },
    }


@router.post(
    "/auth/srp/verify",
    summary="SRP Login Step 2: Verify",
)
async def srp_login_verify(
    payload: SrpVerifyRequest,
    response: Response,
    auth_service: UserAuthService = Depends(get_user_auth_service),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """SRP login step 2: verify client proof and return tokens."""
    from redis.asyncio import Redis as AsyncRedis

    from app.common.auth import create_access_token, create_refresh_token
    from app.core.config import settings
    from app.domain.user.login_security import (
        LoginRateLimiter,
        SessionManager,
        TOTPService,
    )

    username = payload.username
    client_public = payload.client_public_ephemeral
    client_proof = payload.client_proof
    totp_code = payload.totp_code

    user = await auth_service._user_repo.get_by_username(username)
    if (
        user is None
        or not user.hashed_password
        or not user.hashed_password.startswith("SRP:")
    ):
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
        server_secret = await redis.get(srp_key)
        if not server_secret:
            raise AuthenticationRequiredError(
                "SRP session expired, please reinitialize"
            )
        await redis.delete(srp_key)

        success, server_proof_hex = _srp_verify_session(
            server_secret_hex=server_secret,
            client_public_hex=client_public,
            salt_hex=stored_salt,
            username=username,
            verifier_hex=stored_verifier,
            client_proof_hex=client_proof,
        )

        if not success:
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
            from app.common.auth import create_2fa_pending_token

            temp_token = create_2fa_pending_token(user.id)
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
            if totp_code is None:
                raise AuthenticationRequiredError("Invalid 2FA code")
            is_valid_totp = await totp_service.verify_2fa(user.id, totp_code)
            if not is_valid_totp:
                raise AuthenticationRequiredError("Invalid 2FA code")

        access_token = create_access_token(user.id, handle=user.username)
        refresh_token = create_refresh_token(user.id)
        session_mgr = SessionManager(
            AsyncRedis.from_url(settings.redis_url, decode_responses=False)
        )
        session_id = await session_mgr.create_session(user.id)

        response.set_cookie(
            "REFRESH_TOKEN",
            refresh_token,
            httponly=True,
            secure=settings.environment not in ("development", "test"),
            samesite="lax",
            path="/",
        )
        response.set_cookie(
            "SESSION_ID",
            session_id,
            httponly=True,
            secure=settings.environment not in ("development", "test"),
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
                "serverProof": server_proof_hex,
                "requires2FA": False,
                "sessionId": session_id,
            },
        }
    finally:
        await redis.aclose()


@router.post(
    "/auth/verify-2fa",
    summary="Complete a 2FA-gated login",
)
async def verify_2fa_login(
    payload: dict,
    response: Response,
    auth_service: UserAuthService = Depends(get_user_auth_service),
) -> dict:
    """Second step of a 2FA login: exchange the short-lived ``2fa_pending``
    token from the password/SRP step plus a TOTP code (or a one-time backup
    code) for real session tokens. Reference contract: POST {temp_token, code}."""
    from redis.asyncio import Redis as AsyncRedis

    from app.common.auth import create_access_token, create_refresh_token
    from app.core.config import settings
    from app.domain.user.login_security import SessionManager, TOTPService

    temp_token = payload.get("temp_token") or ""
    code = (payload.get("code") or "").strip()
    if not temp_token or not code:
        raise BadRequestError("temp_token and code are required")

    claims = decode_token(temp_token)
    if claims.get("type") != "2fa_pending":
        raise AuthenticationRequiredError("Invalid 2FA session token")
    try:
        user_id = int(claims.get("sub") or "")
    except (TypeError, ValueError) as exc:
        raise AuthenticationRequiredError("Invalid 2FA session token") from exc

    redis = AsyncRedis.from_url(settings.redis_url, decode_responses=False)
    try:
        totp_service = TOTPService(redis)
        used_backup_code = False
        if not await totp_service.verify_2fa(user_id, code):
            if await totp_service.verify_backup_code(user_id, code):
                used_backup_code = True
            else:
                raise AuthenticationRequiredError("Invalid 2FA code")

        try:
            user, profile = await auth_service.get_user_with_profile(user_id)
        except ValueError as exc:
            raise AuthenticationRequiredError(str(exc)) from exc

        access_token = create_access_token(user.id, handle=user.username)
        refresh_token = create_refresh_token(user.id)
        session_id = await SessionManager(redis).create_session(user.id)

        response.set_cookie(
            "REFRESH_TOKEN",
            refresh_token,
            httponly=True,
            secure=settings.environment not in ("development", "test"),
            samesite="lax",
            path="/",
        )
        response.set_cookie(
            "SESSION_ID",
            session_id,
            httponly=True,
            secure=settings.environment not in ("development", "test"),
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
            "message": (
                "Login successfully. Note: This backup code has expired. "
                "Please generate a new backup code for future use."
                if used_backup_code
                else "Login successfully."
            ),
            "data": {
                "user": user_dto,
                "accessToken": access_token,
                "requires2FA": False,
                "usedBackupCode": used_backup_code,
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

    sub = payload.get("sub")
    if sub is None:
        raise AuthenticationRequiredError("Invalid token subject")
    try:
        user_id = int(sub)
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
        secure=settings.environment not in ("development", "test"),
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
) -> dict:
    # Logout is idempotent: even if the refresh cookie is missing or expired,
    # the client should receive cookie-clearing headers and a success response.
    response.delete_cookie(
        "REFRESH_TOKEN",
        path="/",
    )
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
    payload: SudoAuthRequest,
    auth_user: AuthUserInfo = Depends(require_auth_user),
    auth_service: UserAuthService = Depends(get_user_auth_service),
) -> dict:
    from redis.asyncio import Redis as AsyncRedis

    from app.core.config import settings
    from app.domain.user.login_security import TOTPService

    method = payload.method
    credentials = payload.credentials

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

        if not await asyncio.to_thread(
            bcrypt.checkpw,
            password.encode("utf-8"),
            user.hashed_password.encode("utf-8"),
        ):
            raise AuthenticationRequiredError("Invalid password")

        return {
            "code": 200,
            "message": "Sudo mode activated.",
            "data": {"verified": True},
        }

    elif method == "srp":
        client_ephemeral = credentials.get("clientPublicEphemeral")
        client_proof = credentials.get("clientProof")

        user, _profile = await auth_service.get_user_with_profile(auth_user.user_id)
        if not user.hashed_password or not user.hashed_password.startswith("SRP:"):
            raise AuthenticationRequiredError(
                "SRP authentication not available for this account"
            )

        parts = user.hashed_password.split(":", 2)
        if len(parts) != 3:
            raise AuthenticationRequiredError("Corrupted SRP data")
        stored_salt, stored_verifier = parts[1], parts[2]

        redis = AsyncRedis.from_url(settings.redis_url, decode_responses=True)
        try:
            srp_key = f"srp:sudo:{auth_user.user_id}"

            if not client_ephemeral and not client_proof:
                # SRP Step 1: Generate server ephemeral and store session state
                server_public, server_secret = _srp_generate_ephemeral(stored_verifier)

                # Store server secret in Redis (expires in 5 minutes)
                await redis.setex(srp_key, 300, server_secret)

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
                raise BadRequestError(
                    "Both clientPublicEphemeral and clientProof are required"
                )

            server_secret = await redis.get(srp_key)
            if not server_secret:
                raise AuthenticationRequiredError(
                    "SRP session expired, please reinitialize"
                )
            await redis.delete(srp_key)

            success, server_proof_hex = _srp_verify_session(
                server_secret_hex=server_secret,
                client_public_hex=client_ephemeral,
                salt_hex=stored_salt,
                username=user.username,
                verifier_hex=stored_verifier,
                client_proof_hex=client_proof,
            )

            if not success:
                raise AuthenticationRequiredError("Invalid SRP proof")

            return {
                "code": 200,
                "message": "Sudo mode activated via SRP.",
                "data": {
                    "verified": True,
                    "serverProof": server_proof_hex,
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
    auth_user: AuthUserInfo = Depends(require_auth_user),
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
    payload: PutUserIdentityRequest,
    auth_user: AuthUserInfo = Depends(require_auth_user),
    realname_service: UserRealNameService = Depends(get_user_realname_service),
) -> dict:
    if auth_user.user_id != user_id:
        raise ForbiddenError("Only the user themselves can update identity.")
    stored = await realname_service.create_or_update_user_identity(
        user_id=user_id,
        real_name=payload.real_name,
        student_id=payload.student_id,
        grade=payload.grade,
        major=payload.major,
        class_name=payload.class_name,
    )
    return {"code": 200, "message": "Success", "data": {"identity": stored}}


@router.patch(
    "/{userId}/identity",
    summary="Update User Real Name Identity Info (partial)",
)
async def patch_user_identity(
    user_id: Annotated[int, Path(ge=1, alias="userId")],
    payload: PatchUserIdentityRequest,
    auth_user: AuthUserInfo = Depends(require_auth_user),
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
        "realName": payload.real_name
        if payload.real_name is not None
        else base["realName"],
        "studentId": payload.student_id
        if payload.student_id is not None
        else base["studentId"],
        "grade": payload.grade if payload.grade is not None else base["grade"],
        "major": payload.major if payload.major is not None else base["major"],
        "className": payload.class_name
        if payload.class_name is not None
        else base["className"],
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
    auth_user: AuthUserInfo = Depends(require_auth_user),
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


@router.get(
    "/me/sessions",
    summary="List active sessions",
)
async def list_sessions(
    auth_user: AuthUserInfo = Depends(require_auth_user),
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
    auth_user: AuthUserInfo = Depends(require_auth_user),
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
    auth_user: AuthUserInfo = Depends(require_auth_user),
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
    payload: ForgotPasswordRequest,
    auth_service: UserAuthService = Depends(get_user_auth_service),
) -> dict:
    from redis.asyncio import Redis as AsyncRedis

    from app.core.config import settings
    from app.core.email import get_email_sender
    from app.domain.user.login_security import PasswordResetService

    email = payload.email

    user = await auth_service.get_user_by_email(email)
    if user is None:
        return {
            "code": 200,
            "message": "If the email exists, a reset link has been sent.",
        }

    redis = AsyncRedis.from_url(settings.redis_url, decode_responses=False)
    try:
        reset_service = PasswordResetService(redis)
        token = await reset_service.create_reset_token(
            user.id, email, username=user.username
        )

        sender = get_email_sender()
        reset_url = (
            f"{settings.frontend_url}/account/recover/password/verify?token={token}"
        )
        subject = "[Cheese] Password Reset Request"
        body_html = f"""
        <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
            <h2 style="color: #333;">Password Reset</h2>
            <p>You requested to reset your password. Click the link below:</p>
            <p><a href="{reset_url}" style="color: #007bff;">{reset_url}</a></p>
            <p>This link will expire in 30 minutes.</p>
            <p style="color: #666; font-size: 12px;">
              If you didn't request this, please ignore this email.
            </p>
        </div>
        """
        body_text = (
            f"Reset your password: {reset_url}\nThis link expires in 30 minutes."
        )
        await sender.send(
            to=email, subject=subject, body_html=body_html, body_text=body_text
        )

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
    payload: ResetPasswordRequest,
    auth_service: UserAuthService = Depends(get_user_auth_service),
) -> dict:
    from redis.asyncio import Redis as AsyncRedis

    from app.core.config import settings
    from app.domain.user.login_security import PasswordResetService

    token = payload.token
    new_password = payload.password
    if not new_password:
        raise BadRequestError("password is required")

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
    payload: ForgotPasswordRequest,
    auth_service: UserAuthService = Depends(get_user_auth_service),
) -> dict:
    import re

    from redis.asyncio import Redis as AsyncRedis

    from app.core.config import settings
    from app.core.email import get_email_sender
    from app.domain.user.login_security import PasswordResetService

    email = payload.email

    email_regex = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
    if not re.match(email_regex, email):
        raise UnprocessableEntityError("Invalid email address format")

    user = await auth_service.get_user_by_email(email)
    if user is None:
        return {
            "code": 200,
            "message": "If the email exists, a reset link has been sent.",
        }

    redis = AsyncRedis.from_url(settings.redis_url, decode_responses=False)
    try:
        reset_service = PasswordResetService(redis)
        token = await reset_service.create_reset_token(user.id, email, user.username)

        sender = get_email_sender()
        reset_url = (
            f"{settings.frontend_url}/account/recover/password/verify?token={token}"
        )
        subject = "[Cheese] Password Reset Request"
        body_html = f"""
        <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
            <h2 style="color: #333;">Password Reset</h2>
            <p>You requested to reset your password. Click the link below:</p>
            <p><a href="{reset_url}" style="color: #007bff;">{reset_url}</a></p>
            <p>This link will expire in 30 minutes.</p>
            <p style="color: #666; font-size: 12px;">
              If you didn't request this, please ignore this email.
            </p>
        </div>
        """
        body_text = (
            f"Reset your password: {reset_url}\nThis link expires in 30 minutes."
        )
        await sender.send(
            to=email, subject=subject, body_html=body_html, body_text=body_text
        )

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
    payload: ResetPasswordRequest,
    auth_service: UserAuthService = Depends(get_user_auth_service),
) -> dict:
    from redis.asyncio import Redis as AsyncRedis

    from app.core.config import settings
    from app.domain.user.login_security import PasswordResetService

    token = payload.token
    new_password = payload.password
    srp_salt = payload.srp_salt
    srp_verifier = payload.srp_verifier

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
    page_start: int | None = Query(default=None, alias="pageStart"),
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
    page_start: int | None = Query(default=None, alias="pageStart"),
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
    auth_user: AuthUserInfo = Depends(require_auth_user),
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
    page_start: int | None = Query(default=None, alias="pageStart"),
    page_size: int = Query(default=20, ge=1, le=100, alias="pageSize"),
    auth_user: AuthUserInfo = Depends(require_auth_user),
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


def _qr_data_uri(payload: str) -> str:
    """PNG data URI of a QR code (reference contract returns a ready-to-render
    <img src> value alongside the otpauth URL)."""
    import segno

    return segno.make(payload).png_data_uri(scale=5)


@router.post(
    "/{userId}/2fa/enable",
    summary="Start or confirm 2FA setup for user",
)
async def enable_user_2fa(
    user_id: Annotated[int, Path(ge=0, alias="userId")],
    payload: dict = Body(default={}),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    auth_service: UserAuthService = Depends(get_user_auth_service),
) -> dict:
    """Two-phase, reference contract: no body → generate a secret and hand it
    to the client (nothing persisted yet); {secret, code} → verify the live
    code against that secret, persist it, and return the one-time backup
    codes."""
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

        if await totp_service.is_2fa_enabled(auth_user.user_id):
            raise BadRequestError("2FA is already enabled")

        user, _profile = await auth_service.get_user_with_profile(auth_user.user_id)
        account_name = user.email or user.username

        if code:
            if not secret:
                raise BadRequestError("secret is required for confirmation")
            ok = await totp_service.enable_2fa(auth_user.user_id, secret, code)
            if not ok:
                raise UnprocessableEntityError("Invalid or expired verification code")
            backup_codes = await totp_service.generate_backup_codes(auth_user.user_id)
            otpauth_url = totp_service.get_provisioning_uri(secret, account_name)
            return {
                "code": 201,
                "message": "2FA enabled successfully",
                "data": {
                    "secret": secret,
                    "otpauth_url": otpauth_url,
                    "qrcode": _qr_data_uri(otpauth_url),
                    "backup_codes": backup_codes,
                },
            }

        new_secret = totp_service.generate_secret()
        otpauth_url = totp_service.get_provisioning_uri(new_secret, account_name)
        return {
            "code": 200,
            "message": "TOTP secret generated successfully",
            "data": {
                "secret": new_secret,
                "otpauth_url": otpauth_url,
                "qrcode": _qr_data_uri(otpauth_url),
                "backup_codes": [],
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
    auth_user: AuthUserInfo = Depends(require_auth_user),
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

        # Reference contract: no code in the request body (the client gates
        # this behind sudo re-verification). If one IS provided, check it.
        if code and not await totp_service.verify_2fa(auth_user.user_id, code):
            raise AuthenticationRequiredError("Invalid 2FA code")

        await totp_service.disable_2fa(auth_user.user_id)

        return {
            "code": 200,
            "message": "2FA disabled successfully",
            "data": {"success": True},
        }
    finally:
        await redis.aclose()


@router.get(
    "/{userId}/2fa/status",
    summary="Get 2FA status for user",
)
async def get_user_2fa_status(
    user_id: Annotated[int, Path(ge=0, alias="userId")],
    auth_user: AuthUserInfo = Depends(require_auth_user),
    session: AsyncSession = Depends(get_db),
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
        always_required = await totp_service.is_always_required(user_id)
        passkeys = await PasskeyRepository(session).list_by_user(user_id)

        return {
            "code": 200,
            "message": "Get 2FA status successfully",
            "data": {
                "enabled": enabled,
                "has_passkey": len(passkeys) > 0,
                "always_required": always_required,
            },
        }
    finally:
        await redis.aclose()


@router.post(
    "/{userId}/2fa/backup-codes",
    summary="Regenerate 2FA backup codes",
)
async def regenerate_backup_codes(
    user_id: Annotated[int, Path(ge=0, alias="userId")],
    auth_user: AuthUserInfo = Depends(require_auth_user),
) -> dict:
    from redis.asyncio import Redis as AsyncRedis

    from app.core.config import settings
    from app.domain.user.login_security import TOTPService

    if auth_user.user_id != user_id:
        raise ForbiddenError("Only the user themselves can manage backup codes.")

    redis = AsyncRedis.from_url(settings.redis_url, decode_responses=False)
    try:
        totp_service = TOTPService(redis)

        if not await totp_service.is_2fa_enabled(user_id):
            raise BadRequestError("2FA is not enabled")

        backup_codes = await totp_service.generate_backup_codes(user_id)
        return {
            "code": 201,
            "message": "New backup codes generated successfully",
            "data": {"backup_codes": backup_codes},
        }
    finally:
        await redis.aclose()


@router.put(
    "/{userId}/2fa/settings",
    summary="Update 2FA settings",
)
async def update_2fa_settings(
    user_id: Annotated[int, Path(ge=0, alias="userId")],
    payload: dict = Body(default={}),
    auth_user: AuthUserInfo = Depends(require_auth_user),
) -> dict:
    from redis.asyncio import Redis as AsyncRedis

    from app.core.config import settings
    from app.domain.user.login_security import TOTPService

    if auth_user.user_id != user_id:
        raise ForbiddenError("Only the user themselves can change 2FA settings.")

    always_required = payload.get("always_required")
    if not isinstance(always_required, bool):
        raise BadRequestError("always_required (boolean) is required")

    redis = AsyncRedis.from_url(settings.redis_url, decode_responses=False)
    try:
        totp_service = TOTPService(redis)
        await totp_service.set_always_required(user_id, always_required)
        return {
            "code": 200,
            "message": "2FA settings updated successfully",
            "data": {"success": True, "always_required": always_required},
        }
    finally:
        await redis.aclose()


def _challenge_from_credential(credential: dict) -> str:
    """Recover the challenge echoed inside the WebAuthn clientDataJSON. The
    reference contract sends only the credential — the server must not trust a
    separately-supplied challenge anyway."""
    import base64
    import json as _json

    try:
        raw = credential["response"]["clientDataJSON"]
        padded = raw + "=" * (-len(raw) % 4)
        client_data = _json.loads(base64.urlsafe_b64decode(padded))
        challenge = client_data["challenge"]
        if not isinstance(challenge, str) or not challenge:
            raise KeyError("challenge")
        return challenge
    except (KeyError, TypeError, ValueError) as exc:
        raise BadRequestError("Malformed WebAuthn credential") from exc


@router.post(
    "/{userId}/passkeys/options",
    summary="Generate passkey registration options",
)
async def passkey_register_challenge(
    user_id: Annotated[int, Path(ge=0, alias="userId")],
    auth_user: AuthUserInfo = Depends(require_auth_user),
    auth_service: UserAuthService = Depends(get_user_auth_service),
    passkey_service: PasskeyService = Depends(get_passkey_service),
) -> dict:
    import json

    from redis.asyncio import Redis as AsyncRedis

    from app.core.config import settings

    if auth_user.user_id != user_id:
        raise ForbiddenError("Only the user themselves can register a passkey.")

    user, profile = await auth_service.get_user_with_profile(auth_user.user_id)

    options = await passkey_service.generate_registration_options(
        user_id=auth_user.user_id,
        username=user.username,
        display_name=profile.nickname if profile else user.username,
    )

    redis = AsyncRedis.from_url(settings.redis_url, decode_responses=False)
    try:
        challenge_key = f"passkey:challenge:{auth_user.user_id}:{options['challenge']}"
        await redis.set(
            challenge_key, json.dumps({"userId": auth_user.user_id}), ex=300
        )
    finally:
        await redis.aclose()

    return {
        "code": 200,
        "message": "OK",
        "data": {"options": options},
    }


@router.post(
    "/{userId}/passkeys",
    summary="Verify passkey registration",
)
async def passkey_register_verify(
    user_id: Annotated[int, Path(ge=0, alias="userId")],
    payload: dict = Body(default={}),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    passkey_service: PasskeyService = Depends(get_passkey_service),
) -> dict:
    from redis.asyncio import Redis as AsyncRedis

    from app.core.config import settings

    if auth_user.user_id != user_id:
        raise ForbiddenError("Only the user themselves can register a passkey.")

    credential = payload.get("response")
    if not isinstance(credential, dict):
        raise BadRequestError("response (WebAuthn credential) is required")
    challenge = _challenge_from_credential(credential)

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
    "/auth/passkey/options",
    summary="Generate passkey authentication options",
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
    "/auth/passkey/verify",
    summary="Verify passkey authentication",
)
async def passkey_authenticate_verify(
    response: Response,
    payload: dict = Body(default={}),
    passkey_service: PasskeyService = Depends(get_passkey_service),
    auth_service: UserAuthService = Depends(get_user_auth_service),
) -> dict:
    from redis.asyncio import Redis as AsyncRedis

    from app.core.config import settings
    from app.domain.user.login_security import SessionManager

    credential = payload.get("response")
    if not isinstance(credential, dict):
        raise BadRequestError("response (WebAuthn credential) is required")
    challenge = _challenge_from_credential(credential)

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
        secure=settings.environment not in ("development", "test"),
        samesite="lax",
        path="/",
    )
    response.set_cookie(
        "SESSION_ID",
        session_id,
        httponly=True,
        secure=settings.environment not in ("development", "test"),
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
    auth_user: AuthUserInfo = Depends(require_auth_user),
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
    auth_user: AuthUserInfo = Depends(require_auth_user),
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


def _oauth_frontend_url(path: str, **params: str | None) -> str:
    """Build a frontend landing URL (success/error) for the browser redirect."""
    from urllib.parse import urlencode

    query = urlencode({k: v for k, v in params.items() if v is not None})
    return f"{settings.frontend_url}{path}" + (f"?{query}" if query else "")


@router.get(
    "/auth/oauth/login/{providerId}",
    summary="Redirect to the OAuth provider's authorization page",
)
async def get_oauth_login_url(
    provider_id: Annotated[str, Path(alias="providerId")],
    state: str | None = Query(default=None),
    oauth_service: OAuthService = Depends(get_oauth_service),
) -> RedirectResponse:
    # The frontend navigates the browser straight to this endpoint, so we
    # 302-redirect to the provider's authorization page. `state` is generated
    # by the frontend (CSRF) and passed through to the provider unchanged.
    try:
        auth_url = oauth_service.generate_authorization_url(provider_id, state)
    except NotFoundError:
        raise NotFoundError(
            f"OAuth provider '{provider_id}' not found or not enabled"
        ) from None

    return RedirectResponse(auth_url, status_code=302)


@router.get(
    "/auth/oauth/callback/{providerId}",
    summary="Handle OAuth callback and log the user in",
)
async def handle_oauth_callback(
    provider_id: Annotated[str, Path(alias="providerId")],
    code: str = Query(...),
    state: str | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    oauth_service: OAuthService = Depends(get_oauth_service),
    auth_service: UserAuthService = Depends(get_user_auth_service),
) -> RedirectResponse:
    # Step 1: exchange the code and fetch the provider profile. Any failure here
    # is an authentication problem — bounce to the frontend error page.
    try:
        _access_token, user_info = await oauth_service.handle_callback(
            provider_id=provider_id,
            code=code,
            state=state,
        )
    except Exception:
        logger.exception("OAuth callback: provider exchange failed for %s", provider_id)
        return RedirectResponse(
            _oauth_frontend_url(
                settings.frontend_oauth_error_path,
                message="oauth_failed",
                provider=provider_id,
            ),
            status_code=302,
        )

    # Step 2: resolve the local account — existing binding, else match by email,
    # else auto-provision a password-less account — then link and issue tokens.
    # oauth_service and auth_service share this request's session, so a rollback
    # here undoes any partial user/connection writes.
    try:
        existing = await oauth_service.get_connection_by_provider(
            provider_id=provider_id,
            provider_user_id=user_info.id,
        )

        linked: str | None = None
        if existing:
            user_id = existing["userId"]
        else:
            user = None
            if user_info.email:
                user = await auth_service.get_user_by_email(user_info.email)
            if user is None:
                email = user_info.email or f"ruc-{user_info.id}@oauth.ruc.local"
                user, _profile = await auth_service.register_from_oauth(
                    email=email,
                    nickname=user_info.name
                    or user_info.preferred_username
                    or email.split("@")[0],
                    preferred_username=user_info.preferred_username
                    or user_info.username,
                )
            user_id = user.id
            await oauth_service.create_connection(
                user_id=user_id,
                provider_id=provider_id,
                provider_user_id=user_info.id,
                raw_profile={"email": user_info.email, "name": user_info.name},
            )
            linked = "true"

        user_obj, _profile = await auth_service.get_user_with_profile(user_id)
        access_token_jwt = create_access_token(user_id)
        refresh_token = create_refresh_token(user_id)
    except Exception:
        await session.rollback()
        logger.exception(
            "OAuth callback: account resolution failed for %s", provider_id
        )
        return RedirectResponse(
            _oauth_frontend_url(
                settings.frontend_oauth_error_path,
                message="account_error",
                provider=provider_id,
            ),
            status_code=302,
        )

    redirect = RedirectResponse(
        _oauth_frontend_url(
            settings.frontend_oauth_success_path,
            token=access_token_jwt,
            email=user_obj.email,
            provider=provider_id,
            linked=linked,
        ),
        status_code=302,
    )
    redirect.set_cookie(
        "REFRESH_TOKEN",
        refresh_token,
        httponly=True,
        secure=settings.environment not in ("development", "test"),
        samesite="lax",
        path="/",
    )
    return redirect


@router.post(
    "/auth/oauth/link",
    summary="Link OAuth account to existing user",
)
async def link_oauth_account(
    payload: LinkOAuthRequest,
    auth_user: AuthUserInfo = Depends(require_auth_user),
    oauth_service: OAuthService = Depends(get_oauth_service),
) -> dict:
    provider_id = payload.provider_id
    provider_user_id = payload.provider_user_id
    raw_profile = payload.profile

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
    auth_user: AuthUserInfo = Depends(require_auth_user),
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
    payload: CreateInviteCodeRequest,
    auth_user: AuthUserInfo = Depends(require_auth_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    from app.domain.invite.services import InviteCodeService

    service = InviteCodeService(session)
    invite = await service.create_code(
        max_uses=payload.max_uses,
        created_by=auth_user.user_id,
        note=payload.note,
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
    auth_user: AuthUserInfo = Depends(require_auth_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    from app.domain.invite.services import InviteCodeService

    service = InviteCodeService(session)
    await service.deactivate_code(code_id)
    await session.commit()
    return {"code": 200, "message": "Invite code deactivated.", "data": None}


@router.get(
    "/{userId}/oauth-connections",
    summary="List user OAuth connections",
)
async def list_oauth_connections(
    user_id: Annotated[int, Path(ge=0, alias="userId")],
    auth_user: AuthUserInfo = Depends(require_auth_user),
    oauth_service: OAuthService = Depends(get_oauth_service),
) -> dict:
    if auth_user.user_id != user_id:
        raise ForbiddenError(
            "Only the user themselves can view their OAuth connections."
        )

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
    auth_user: AuthUserInfo = Depends(require_auth_user),
    oauth_service: OAuthService = Depends(get_oauth_service),
) -> dict:
    if auth_user.user_id != user_id:
        raise ForbiddenError(
            "Only the user themselves can unbind their OAuth connections."
        )

    deleted = await oauth_service.delete_connection(connection_id, user_id)

    if not deleted:
        raise NotFoundError("OAuth connection not found")

    return {
        "code": 200,
        "message": "OAuth connection removed successfully.",
    }
