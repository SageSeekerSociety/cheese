from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field

from app.auth.checker import require_auth_user, require_permission
from app.auth.core import Action, AuthUserInfo, Resource
from app.core.errors import BadRequestError, NotFoundError
from app.db.session import get_db
from app.domain.team.membership_services import TeamMembershipService
from app.domain.team.models import ApplicationStatus, Team, TeamMemberRole, TeamUserRelation
from app.domain.team.repositories import TeamMembershipApplicationRepository, TeamRepository
from app.domain.team.services import TeamService
from app.domain.user.repositories import UserProfileRepository, UserRepository

# Number of admin / member examples to surface alongside the count, mirroring
# the Kotlin TeamService implementation (PageRequest.of(0, 3)).
_TEAM_EXAMPLES_LIMIT = 3

# ── Request Models ────────────────────────────────────────────────────────────


class CreateTeamRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(..., min_length=1)
    intro: str = ""
    description: str = ""
    avatar_id: int = Field(default=1, alias="avatarId", gt=0)


class PatchTeamRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str | None = None
    intro: str | None = None
    description: str | None = None
    avatar_id: int | None = Field(default=None, alias="avatarId")


class PatchTeamMemberRoleRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    role: str = Field(..., min_length=1)


class AddTeamMemberRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    user_id: int = Field(..., alias="userId", gt=0)
    role: str = "MEMBER"


class CreateTeamInvitationRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    user_id: int = Field(..., alias="userId", gt=0)
    role: str | None = None
    message: str | None = None


class CreateTeamJoinRequestBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    message: str | None = None


router = APIRouter(prefix="/teams", tags=["Teams"])


async def get_team_service(db=Depends(get_db)) -> TeamService:
    repo = TeamRepository(session=db)
    return TeamService(repo)


async def get_team_membership_service(
    db=Depends(get_db),
) -> TeamMembershipService:
    team_repo = TeamRepository(session=db)
    app_repo = TeamMembershipApplicationRepository(session=db)
    return TeamMembershipService(session=db, team_repo=team_repo, application_repo=app_repo)


def _user_payload(user, profile, *, fallback_id: int) -> dict:
    """Build a User-shaped dict matching frontend `User` type expectations."""
    if user is None:
        return {
            "id": fallback_id,
            "username": "",
            "nickname": "",
            "avatarId": None,
            "intro": "",
            "follow_count": 0,
            "fans_count": 0,
            "question_count": 0,
            "answer_count": 0,
        }
    nickname = profile.nickname if profile and getattr(profile, "nickname", None) else user.username
    return {
        "id": user.id,
        "username": user.username,
        "nickname": nickname,
        "avatarId": profile.avatar_id if profile else None,
        "intro": profile.intro if profile else "",
        "follow_count": 0,
        "fans_count": 0,
        "question_count": 0,
        "answer_count": 0,
    }


def _team_to_api_model(
    team: Team,
    *,
    members: list[TeamUserRelation] | None = None,
    current_user_id: int | None = None,
    users_map: dict | None = None,
    profiles_map: dict | None = None,
) -> dict:
    created_at_ms = int(team.created_at.timestamp() * 1000) if team.created_at is not None else 0
    updated_at_ms = int(team.updated_at.timestamp() * 1000) if team.updated_at is not None else 0

    owner_info = None
    admin_relations: list[TeamUserRelation] = []
    member_relations: list[TeamUserRelation] = []
    joined = False
    user_role = None

    users_map = users_map or {}
    profiles_map = profiles_map or {}

    if members is not None:
        for rel in members:
            if rel.role == TeamMemberRole.OWNER:
                owner_info = _user_payload(
                    users_map.get(rel.user_id),
                    profiles_map.get(rel.user_id),
                    fallback_id=rel.user_id,
                )
            elif rel.role == TeamMemberRole.ADMIN:
                admin_relations.append(rel)
            elif rel.role == TeamMemberRole.MEMBER:
                member_relations.append(rel)

            if current_user_id is not None and rel.user_id == current_user_id:
                joined = True
                role_map = {0: "OWNER", 1: "ADMIN", 2: "MEMBER"}
                user_role = role_map.get(rel.role, "MEMBER")

    # Mirror the Kotlin behaviour: examples are ordered by updated_at DESC and
    # capped at 3 entries (PageRequest.of(0, 3)).
    def _examples(relations: list[TeamUserRelation]) -> list[dict]:
        ordered = sorted(
            relations,
            key=lambda r: r.updated_at if r.updated_at is not None else r.created_at,
            reverse=True,
        )[:_TEAM_EXAMPLES_LIMIT]
        return [
            _user_payload(
                users_map.get(rel.user_id),
                profiles_map.get(rel.user_id),
                fallback_id=rel.user_id,
            )
            for rel in ordered
        ]

    result = {
        "id": team.id,
        "name": team.name,
        "intro": team.intro,
        "description": team.description,
        "avatarId": team.avatar_id,
        "createdAt": created_at_ms,
        "updatedAt": updated_at_ms,
    }

    if owner_info is not None:
        result["owner"] = owner_info
    if members is not None:
        result["admins"] = {
            "total": len(admin_relations),
            "examples": _examples(admin_relations),
        }
        result["members"] = {
            "total": len(member_relations),
            "examples": _examples(member_relations),
        }
    if current_user_id is not None:
        result["joined"] = joined
        result["role"] = user_role

    return result


def _member_to_api_model(
    rel: TeamUserRelation,
    *,
    users_map: dict | None = None,
    profiles_map: dict | None = None,
) -> dict:
    """TeamMember representation with the full User shape the frontend expects."""
    created_at_ms = int(rel.created_at.timestamp() * 1000) if rel.created_at is not None else 0
    updated_at_ms = int(rel.updated_at.timestamp() * 1000) if rel.updated_at is not None else 0
    # Map numeric role to string name (OWNER / ADMIN / MEMBER)
    role_map = {
        0: "OWNER",
        1: "ADMIN",
        2: "MEMBER",
    }
    role_name = role_map.get(rel.role, "MEMBER")
    users_map = users_map or {}
    profiles_map = profiles_map or {}
    user_payload = _user_payload(
        users_map.get(rel.user_id),
        profiles_map.get(rel.user_id),
        fallback_id=rel.user_id,
    )
    return {
        "role": role_name,
        "user": user_payload,
        "userId": rel.user_id,
        "createdAt": created_at_ms,
        "updatedAt": updated_at_ms,
    }


async def _load_team_user_maps(db, members: list[TeamUserRelation]) -> tuple[dict, dict]:
    """Bulk-load User + UserProfile records for all member relations."""
    user_ids = list({rel.user_id for rel in members})
    if not user_ids:
        return {}, {}
    user_repo = UserRepository(session=db)
    profile_repo = UserProfileRepository(session=db)
    users_map = await user_repo.get_by_ids(user_ids)
    profiles_map = await profile_repo.get_profiles_by_user_ids(user_ids)
    return users_map, profiles_map


def _team_summary_payload(team: Team | None, *, fallback_id: int) -> dict:
    """TeamSummary as expected by the frontend (id/name/intro/avatarId)."""
    if team is None:
        return {"id": fallback_id, "name": "", "intro": "", "avatarId": None}
    return {
        "id": team.id,
        "name": team.name,
        "intro": team.intro,
        "avatarId": team.avatar_id,
    }


def _application_to_api_model(
    app,
    *,
    users_map: dict | None = None,
    profiles_map: dict | None = None,
    teams_map: dict | None = None,
) -> dict:
    """TeamMembershipApplication payload aligned with the frontend type.

    The frontend ``TeamMembershipApplication`` (cheese-frontend types/teams.ts)
    embeds full ``user``, ``team``, ``initiator`` and optional ``processedBy``
    objects. Bare-id responses caused Members.vue's "已发送邀请" tab to render
    blank rows. Pass bulk-loaded users/profiles/teams maps to keep this O(1)
    per row in list endpoints.
    """
    users_map = users_map or {}
    profiles_map = profiles_map or {}
    teams_map = teams_map or {}

    payload: dict = {
        "id": app.id,
        "userId": app.user_id,
        "teamId": app.team_id,
        "user": _user_payload(
            users_map.get(app.user_id),
            profiles_map.get(app.user_id),
            fallback_id=app.user_id,
        ),
        "team": _team_summary_payload(teams_map.get(app.team_id), fallback_id=app.team_id),
        "initiator": _user_payload(
            users_map.get(app.initiator_id),
            profiles_map.get(app.initiator_id),
            fallback_id=app.initiator_id,
        ),
        "type": app.type,
        "status": app.status,
        "role": app.role,
        "message": app.message,
        "processedAt": int(app.processed_at.timestamp() * 1000) if app.processed_at else None,
        "createdAt": int(app.created_at.timestamp() * 1000) if app.created_at else None,
        "updatedAt": int(app.updated_at.timestamp() * 1000) if app.updated_at else None,
    }
    if app.processed_by_id is not None:
        payload["processedBy"] = _user_payload(
            users_map.get(app.processed_by_id),
            profiles_map.get(app.processed_by_id),
            fallback_id=app.processed_by_id,
        )
    else:
        payload["processedBy"] = None
    return payload


async def _load_application_maps(db, apps) -> tuple[dict, dict, dict]:
    """Bulk-fetch User+UserProfile+Team for a batch of applications."""
    if not apps:
        return {}, {}, {}
    user_ids = set()
    team_ids = set()
    for app in apps:
        user_ids.add(app.user_id)
        user_ids.add(app.initiator_id)
        if app.processed_by_id is not None:
            user_ids.add(app.processed_by_id)
        team_ids.add(app.team_id)
    user_repo = UserRepository(session=db)
    profile_repo = UserProfileRepository(session=db)
    team_repo = TeamRepository(session=db)
    users_map = await user_repo.get_by_ids(list(user_ids))
    profiles_map = await profile_repo.get_profiles_by_user_ids(list(user_ids))
    teams_map = await team_repo.get_by_ids(list(team_ids))
    return users_map, profiles_map, teams_map


_ROLE_NAME_TO_VALUE = {
    "OWNER": TeamMemberRole.OWNER,
    "ADMIN": TeamMemberRole.ADMIN,
    "MEMBER": TeamMemberRole.MEMBER,
}


def _parse_role(role_value: str | None, *, allow_owner: bool = False) -> int:
    if role_value is None:
        return TeamMemberRole.MEMBER
    upper = role_value.upper()
    if not allow_owner and upper == "OWNER":
        raise BadRequestError("Cannot set role to OWNER via this endpoint")
    mapped = _ROLE_NAME_TO_VALUE.get(upper)
    if mapped is None:
        raise BadRequestError("Invalid role value")
    return mapped


@router.get(
    "",
    summary="Get Teams",
)
async def get_teams(
    query: str = Query(default="", description="ID or Search Term"),
    page_start: str | None = Query(default=None, alias="pageStart"),
    page_size: int = Query(default=20, ge=1, le=100, alias="pageSize"),
    service: TeamService = Depends(get_team_service),
    db=Depends(get_db),
) -> dict:
    offset = int(page_start) if page_start and page_start.isdigit() else 0
    teams = await service.enumerate_teams(query=query or None, limit=page_size + 1, offset=offset)
    has_more = len(teams) > page_size
    teams_to_emit = teams[:page_size]
    items: list[dict] = []
    for team in teams_to_emit:
        members = list(await service.get_team_members(team_id=team.id))
        users_map, profiles_map = await _load_team_user_maps(db, members)
        items.append(
            _team_to_api_model(
                team,
                members=members,
                users_map=users_map,
                profiles_map=profiles_map,
            )
        )
    next_start = str(offset + page_size) if has_more else None
    return {
        "code": 200,
        "message": "OK",
        "data": {
            "teams": items,
            "page": {
                "pageStart": page_start or "0",
                "pageSize": len(items),
                "hasMore": has_more,
                "nextStart": next_start,
            },
        },
    }


@router.get(
    "/my-teams",
    summary="Query My Teams",
)
async def get_my_teams(
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: TeamService = Depends(get_team_service),
    db=Depends(get_db),
) -> dict:
    teams = await service.get_teams_of_user(user_id=auth_user.user_id)
    items = []
    for team in teams:
        members = list(await service.get_team_members(team_id=team.id))
        users_map, profiles_map = await _load_team_user_maps(db, members)
        items.append(
            _team_to_api_model(
                team,
                members=members,
                current_user_id=auth_user.user_id,
                users_map=users_map,
                profiles_map=profiles_map,
            )
        )
    return {
        "code": 200,
        "message": "OK",
        "data": {"teams": items},
    }


@router.get(
    "/{teamId}",
    summary="Query Team",
)
async def get_team(
    team_id: Annotated[int, Path(ge=1, alias="teamId")],
    service: TeamService = Depends(get_team_service),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    db=Depends(get_db),
) -> dict:
    team = await service.get_team(team_id=team_id)
    if team is None:
        raise NotFoundError("Resource team not found", data={"type": "team", "id": team_id})

    members = list(await service.get_team_members(team_id=team_id))
    users_map, profiles_map = await _load_team_user_maps(db, members)
    current_user_id = auth_user.user_id if auth_user.user_id > 0 else None
    return {
        "code": 200,
        "message": "OK",
        "data": {
            "team": _team_to_api_model(
                team,
                members=members,
                current_user_id=current_user_id,
                users_map=users_map,
                profiles_map=profiles_map,
            )
        },
    }


@router.get(
    "/{teamId}/members",
    summary="Enumerate Team Members",
)
async def get_team_members(
    team_id: Annotated[int, Path(ge=1, alias="teamId")],
    queryRealNameStatus: bool = Query(default=False),
    # NT @Auth("team:view:membership") requires MEMBER role or higher; this
    # route used to have no auth at all, so anyone — even anonymous — could
    # enumerate any team's roster.
    auth_user: AuthUserInfo = require_permission(Action.READ, Resource.TEAM_MEMBERSHIP, "teamId"),
    service: TeamService = Depends(get_team_service),
    db=Depends(get_db),
) -> dict:
    """Return team members for a given team."""
    _ = auth_user
    relations = list(await service.get_team_members(team_id=team_id))
    users_map, profiles_map = await _load_team_user_maps(db, relations)
    members = [
        _member_to_api_model(rel, users_map=users_map, profiles_map=profiles_map)
        for rel in relations
    ]
    # Compute allMembersVerified: check real-name status for each member
    all_verified: bool | None = None
    if queryRealNameStatus:
        from app.domain.user.repositories import UserRealNameRepository

        realname_repo = UserRealNameRepository(session=db)
        member_user_ids = [rel.user_id for rel in relations]
        all_verified = True
        for uid in member_user_ids:
            if not await realname_repo.has_identity(uid):
                all_verified = False
                break
    return {
        "code": 200,
        "message": "OK",
        "data": {
            "members": members,
            "allMembersVerified": all_verified,
        },
    }


@router.post(
    "",
    summary="Create Team",
    status_code=status.HTTP_201_CREATED,
)
async def create_team(
    payload: CreateTeamRequest,
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: TeamService = Depends(get_team_service),
    db=Depends(get_db),
) -> dict:
    team = await service.create_team(
        name=payload.name,
        intro=payload.intro,
        description=payload.description,
        avatar_id=payload.avatar_id,
        owner_id=auth_user.user_id,
    )
    members = list(await service.get_team_members(team_id=team.id))
    users_map, profiles_map = await _load_team_user_maps(db, members)
    return {
        "code": 201,
        "message": "Team created",
        "data": {
            "team": _team_to_api_model(
                team,
                members=members,
                current_user_id=auth_user.user_id,
                users_map=users_map,
                profiles_map=profiles_map,
            )
        },
    }


@router.patch(
    "/{teamId}",
    summary="Update Team",
)
async def patch_team(
    team_id: Annotated[int, Path(ge=1, alias="teamId")],
    payload: PatchTeamRequest,
    auth_user: AuthUserInfo = require_permission(Action.UPDATE, Resource.TEAM, "teamId"),
    service: TeamService = Depends(get_team_service),
    db=Depends(get_db),
) -> dict:
    team = await service.update_team(
        team_id=team_id,
        actor_user_id=auth_user.user_id,
        name=payload.name,
        intro=payload.intro,
        description=payload.description,
        avatar_id=payload.avatar_id,
    )
    members = list(await service.get_team_members(team_id=team_id))
    users_map, profiles_map = await _load_team_user_maps(db, members)
    return {
        "code": 200,
        "message": "OK",
        "data": {
            "team": _team_to_api_model(
                team,
                members=members,
                current_user_id=auth_user.user_id,
                users_map=users_map,
                profiles_map=profiles_map,
            )
        },
    }


@router.delete(
    "/{teamId}",
    summary="Disband Team",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_team(
    team_id: Annotated[int, Path(ge=1, alias="teamId")],
    auth_user: AuthUserInfo = require_permission(Action.DELETE, Resource.TEAM, "teamId"),
    service: TeamService = Depends(get_team_service),
) -> Response:
    await service.delete_team(team_id=team_id, actor_user_id=auth_user.user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "/{teamId}/members/{userId}",
    summary="Remove Team Member",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_team_member(
    team_id: Annotated[int, Path(ge=1, alias="teamId")],
    user_id: Annotated[int, Path(ge=1, alias="userId")],
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: TeamService = Depends(get_team_service),
) -> Response:
    await service.remove_team_member(
        team_id=team_id,
        target_user_id=user_id,
        actor_user_id=auth_user.user_id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.patch(
    "/{teamId}/members/{userId}",
    summary="Update Team Member Role",
)
async def patch_team_member_role(
    team_id: Annotated[int, Path(ge=1, alias="teamId")],
    user_id: Annotated[int, Path(ge=1, alias="userId")],
    payload: PatchTeamMemberRoleRequest,
    auth_user: AuthUserInfo = require_permission(Action.UPDATE, Resource.TEAM_MEMBERSHIP, "teamId"),
    service: TeamService = Depends(get_team_service),
    db=Depends(get_db),
) -> dict:
    mapped_role = _parse_role(payload.role, allow_owner=False)

    await service.update_team_member_role(
        team_id=team_id,
        target_user_id=user_id,
        actor_user_id=auth_user.user_id,
        new_role=mapped_role,
    )
    team = await service.get_team(team_id)
    if team is None:
        raise NotFoundError("Resource team not found", data={"type": "team", "id": team_id})
    members = list(await service.get_team_members(team_id=team_id))
    users_map, profiles_map = await _load_team_user_maps(db, members)
    return {
        "code": 200,
        "message": "OK",
        "data": {
            "team": _team_to_api_model(
                team,
                members=members,
                current_user_id=auth_user.user_id,
                users_map=users_map,
                profiles_map=profiles_map,
            )
        },
    }


@router.post(
    "/{teamId}/members",
    summary="Add Team Member",
    status_code=status.HTTP_201_CREATED,
)
async def add_team_member_entry(
    team_id: Annotated[int, Path(ge=1, alias="teamId")],
    payload: AddTeamMemberRequest,
    auth_user: AuthUserInfo = require_permission(Action.CREATE, Resource.TEAM_MEMBERSHIP, "teamId"),
    service: TeamService = Depends(get_team_service),
    db=Depends(get_db),
) -> dict:
    _ = auth_user
    user_id = payload.user_id
    role_str = payload.role.upper()

    role_map = {"MEMBER": TeamMemberRole.MEMBER, "ADMIN": TeamMemberRole.ADMIN}
    role_val = role_map.get(role_str)
    if role_val is None:
        raise BadRequestError(f"Invalid role: {role_str}. Must be MEMBER or ADMIN")

    team = await service.get_team(team_id)
    if team is None:
        raise NotFoundError("Team not found")

    from app.domain.team.services import check_team_locking_status

    await check_team_locking_status(db, team_id)

    relation = await service._repo.add_member(team_id, user_id, role_val)
    return {
        "code": 201,
        "message": "Created",
        "data": {
            "member": {
                "userId": relation.user_id,
                "role": role_str,
                "createdAt": int(relation.created_at.timestamp() * 1000),
                "updatedAt": int(relation.updated_at.timestamp() * 1000),
            }
        },
    }


@router.get(
    "/{teamId}/join-requests",
    summary="List Team Join Requests",
)
async def list_team_join_requests(
    team_id: Annotated[int, Path(ge=1, alias="teamId")],
    status: str | None = Query(default=None),
    pageStart: int | None = Query(default=None),
    pageSize: int | None = Query(default=None),
    auth_user: AuthUserInfo = require_permission(Action.READ, Resource.TEAM_REQUEST, "teamId"),
    membership_service: TeamMembershipService = Depends(get_team_membership_service),
    db=Depends(get_db),
) -> dict:
    status_enum: ApplicationStatus | None = None
    if status is not None:
        upper = status.upper()
        if upper in {"PENDING", "APPROVED", "REJECTED", "ACCEPTED", "DECLINED", "CANCELED"}:
            status_enum = ApplicationStatus[upper]
        else:
            raise BadRequestError(f"Invalid status: {status}")
    apps, page = await membership_service.list_team_join_requests(
        requesting_user_id=auth_user.user_id,
        team_id=team_id,
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
            "applications": items,
            "page": page,
        },
    }


@router.get(
    "/{teamId}/requests",
    summary="List Team Requests",
)
async def list_team_requests_alias(
    team_id: Annotated[int, Path(ge=1, alias="teamId")],
    status: str | None = Query(default=None),
    pageStart: int | None = Query(default=None),
    pageSize: int | None = Query(default=None),
    auth_user: AuthUserInfo = require_permission(Action.READ, Resource.TEAM_REQUEST, "teamId"),
    membership_service: TeamMembershipService = Depends(get_team_membership_service),
    db=Depends(get_db),
) -> dict:
    return await list_team_join_requests(
        team_id=team_id,
        status=status,
        pageStart=pageStart,
        pageSize=pageSize,
        auth_user=auth_user,
        membership_service=membership_service,
        db=db,
    )


@router.get(
    "/{teamId}/invitations",
    summary="List Team Invitations",
)
async def list_team_invitations(
    team_id: Annotated[int, Path(ge=1, alias="teamId")],
    status: str | None = Query(default=None),
    pageStart: int | None = Query(default=None),
    pageSize: int | None = Query(default=None),
    auth_user: AuthUserInfo = require_permission(Action.READ, Resource.TEAM_INVITATION, "teamId"),
    membership_service: TeamMembershipService = Depends(get_team_membership_service),
    db=Depends(get_db),
) -> dict:
    status_enum: ApplicationStatus | None = None
    if status is not None:
        upper = status.upper()
        if upper in {"PENDING", "APPROVED", "REJECTED", "ACCEPTED", "DECLINED", "CANCELED"}:
            status_enum = ApplicationStatus[upper]
        else:
            raise BadRequestError(f"Invalid status: {status}")
    apps, page = await membership_service.list_team_invitations(
        requesting_user_id=auth_user.user_id,
        team_id=team_id,
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


@router.post(
    "/{teamId}/invitations",
    summary="Create Team Invitation",
    status_code=status.HTTP_201_CREATED,
)
async def create_team_invitation(
    team_id: Annotated[int, Path(ge=1, alias="teamId")],
    payload: CreateTeamInvitationRequest,
    auth_user: AuthUserInfo = require_permission(Action.CREATE, Resource.TEAM_INVITATION, "teamId"),
    membership_service: TeamMembershipService = Depends(get_team_membership_service),
    db=Depends(get_db),
) -> dict:
    role = _parse_role(payload.role, allow_owner=False)
    app = await membership_service.create_team_invitation(
        initiator_user_id=auth_user.user_id,
        team_id=team_id,
        user_id_to_invite=payload.user_id,
        role=role,
        message=payload.message,
    )
    users_map, profiles_map, teams_map = await _load_application_maps(db, [app])
    return {
        "code": 201,
        "message": "Invitation created",
        "data": {
            "invitation": _application_to_api_model(
                app, users_map=users_map, profiles_map=profiles_map, teams_map=teams_map
            )
        },
    }


@router.delete(
    "/{teamId}/invitations/{invitationId}",
    summary="Cancel Team Invitation",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def cancel_team_invitation(
    team_id: Annotated[int, Path(ge=1, alias="teamId")],
    invitation_id: Annotated[int, Path(ge=1, alias="invitationId")],
    auth_user: AuthUserInfo = require_permission(Action.DELETE, Resource.TEAM_INVITATION, "teamId"),
    membership_service: TeamMembershipService = Depends(get_team_membership_service),
) -> Response:
    await membership_service.cancel_team_invitation(
        canceler_user_id=auth_user.user_id,
        team_id=team_id,
        invitation_id=invitation_id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{teamId}/join-requests",
    summary="Create Team Join Request",
    status_code=status.HTTP_201_CREATED,
)
async def create_team_join_request_via_team(
    team_id: Annotated[int, Path(ge=1, alias="teamId")],
    payload: CreateTeamJoinRequestBody,
    auth_user: AuthUserInfo = Depends(require_auth_user),
    membership_service: TeamMembershipService = Depends(get_team_membership_service),
    db=Depends(get_db),
) -> dict:
    app = await membership_service.create_team_join_request(
        user_id=auth_user.user_id,
        team_id=team_id,
        message=payload.message,
    )
    users_map, profiles_map, teams_map = await _load_application_maps(db, [app])
    return {
        "code": 201,
        "message": "Join request created",
        "data": {
            "application": _application_to_api_model(
                app, users_map=users_map, profiles_map=profiles_map, teams_map=teams_map
            )
        },
    }


@router.post(
    "/{teamId}/requests",
    summary="Create Team Request",
    status_code=status.HTTP_201_CREATED,
)
async def create_team_request_alias(
    team_id: Annotated[int, Path(ge=1, alias="teamId")],
    payload: CreateTeamJoinRequestBody,
    auth_user: AuthUserInfo = Depends(require_auth_user),
    membership_service: TeamMembershipService = Depends(get_team_membership_service),
    db=Depends(get_db),
) -> dict:
    return await create_team_join_request_via_team(
        team_id=team_id,
        payload=payload,
        auth_user=auth_user,
        membership_service=membership_service,
        db=db,
    )


@router.post(
    "/{teamId}/join-requests/{requestId}/approve",
    summary="Approve Team Join Request",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def approve_team_join_request(
    team_id: Annotated[int, Path(ge=1, alias="teamId")],
    request_id: Annotated[int, Path(ge=1, alias="requestId")],
    auth_user: AuthUserInfo = require_permission(Action.UPDATE, Resource.TEAM_REQUEST, "teamId"),
    membership_service: TeamMembershipService = Depends(get_team_membership_service),
) -> Response:
    await membership_service.approve_team_join_request(
        approver_user_id=auth_user.user_id,
        team_id=team_id,
        request_id=request_id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{teamId}/requests/{requestId}/approve",
    summary="Approve Team Request",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def approve_team_request_alias(
    team_id: Annotated[int, Path(ge=1, alias="teamId")],
    request_id: Annotated[int, Path(ge=1, alias="requestId")],
    auth_user: AuthUserInfo = require_permission(Action.UPDATE, Resource.TEAM_REQUEST, "teamId"),
    membership_service: TeamMembershipService = Depends(get_team_membership_service),
) -> Response:
    await membership_service.approve_team_join_request(
        approver_user_id=auth_user.user_id,
        team_id=team_id,
        request_id=request_id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{teamId}/join-requests/{requestId}/reject",
    summary="Reject Team Join Request",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def reject_team_join_request(
    team_id: Annotated[int, Path(ge=1, alias="teamId")],
    request_id: Annotated[int, Path(ge=1, alias="requestId")],
    auth_user: AuthUserInfo = require_permission(Action.UPDATE, Resource.TEAM_REQUEST, "teamId"),
    membership_service: TeamMembershipService = Depends(get_team_membership_service),
) -> Response:
    await membership_service.reject_team_join_request(
        rejector_user_id=auth_user.user_id,
        team_id=team_id,
        request_id=request_id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{teamId}/requests/{requestId}/reject",
    summary="Reject Team Request",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def reject_team_request_alias(
    team_id: Annotated[int, Path(ge=1, alias="teamId")],
    request_id: Annotated[int, Path(ge=1, alias="requestId")],
    auth_user: AuthUserInfo = require_permission(Action.UPDATE, Resource.TEAM_REQUEST, "teamId"),
    membership_service: TeamMembershipService = Depends(get_team_membership_service),
) -> Response:
    await membership_service.reject_team_join_request(
        rejector_user_id=auth_user.user_id,
        team_id=team_id,
        request_id=request_id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
