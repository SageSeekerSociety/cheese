from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Response, status

from app.auth.checker import get_auth_user, require_permission
from app.auth.core import Action, AuthUserInfo, Resource
from app.core.errors import BadRequestError, NotFoundError
from app.db.session import get_db
from app.domain.team.membership_services import TeamMembershipService
from app.domain.team.models import ApplicationStatus, Team, TeamMemberRole, TeamUserRelation
from app.domain.team.repositories import TeamMembershipApplicationRepository, TeamRepository
from app.domain.team.services import TeamService

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


def _team_to_api_model(
    team: Team,
    *,
    members: list[TeamUserRelation] | None = None,
    current_user_id: int | None = None,
) -> dict:
    created_at_ms = int(team.created_at.timestamp() * 1000) if team.created_at is not None else 0
    updated_at_ms = int(team.updated_at.timestamp() * 1000) if team.updated_at is not None else 0

    owner_info = None
    admins_total = 0
    members_total = 0
    joined = False
    user_role = None

    if members is not None:
        for rel in members:
            if rel.role == TeamMemberRole.OWNER:
                owner_info = {"id": rel.user_id}
            elif rel.role == TeamMemberRole.ADMIN:
                admins_total += 1
            elif rel.role == TeamMemberRole.MEMBER:
                members_total += 1

            if current_user_id is not None and rel.user_id == current_user_id:
                joined = True
                role_map = {0: "OWNER", 1: "ADMIN", 2: "MEMBER"}
                user_role = role_map.get(rel.role, "MEMBER")

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
        result["admins"] = {"total": admins_total}
        result["members"] = {"total": members_total}
    if current_user_id is not None:
        result["joined"] = joined
        result["role"] = user_role

    return result


def _member_to_api_model(rel: TeamUserRelation) -> dict:
    """Minimal TeamMember representation.

    NOTE: This is a placeholder that only exposes role and timestamps.
    User details can be enriched later by joining with user/profile tables.
    """
    created_at_ms = int(rel.created_at.timestamp() * 1000) if rel.created_at is not None else 0
    updated_at_ms = int(rel.updated_at.timestamp() * 1000) if rel.updated_at is not None else 0
    # Map numeric role to string name (OWNER / ADMIN / MEMBER)
    role_map = {
        0: "OWNER",
        1: "ADMIN",
        2: "MEMBER",
    }
    role_name = role_map.get(rel.role, "MEMBER")
    return {
        "role": role_name,
        "user": {"id": rel.user_id},
        "userId": rel.user_id,
        "createdAt": created_at_ms,
        "updatedAt": updated_at_ms,
    }


def _application_to_api_model(app) -> dict:
    return {
        "id": app.id,
        "userId": app.user_id,
        "teamId": app.team_id,
        "type": app.type,
        "status": app.status,
        "role": app.role,
        "message": app.message,
        "processedBy": app.processed_by_id,
        "processedAt": int(app.processed_at.timestamp() * 1000) if app.processed_at else None,
        "createdAt": int(app.created_at.timestamp() * 1000) if app.created_at else None,
    }


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
) -> dict:
    offset = int(page_start) if page_start and page_start.isdigit() else 0
    teams = await service.enumerate_teams(query=query or None, limit=page_size + 1, offset=offset)
    has_more = len(teams) > page_size
    items = [_team_to_api_model(t) for t in teams[:page_size]]
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
    auth_user: AuthUserInfo = Depends(get_auth_user),
    service: TeamService = Depends(get_team_service),
) -> dict:
    teams = await service.get_teams_of_user(user_id=auth_user.user_id)
    items = []
    for team in teams:
        members = list(await service.get_team_members(team_id=team.id))
        items.append(_team_to_api_model(team, members=members, current_user_id=auth_user.user_id))
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
    auth_user: AuthUserInfo = Depends(get_auth_user),
) -> dict:
    team = await service.get_team(team_id=team_id)
    if team is None:
        raise NotFoundError("Resource team not found", data={"type": "team", "id": team_id})

    members = list(await service.get_team_members(team_id=team_id))
    current_user_id = auth_user.user_id if auth_user.user_id > 0 else None
    return {
        "code": 200,
        "message": "OK",
        "data": {
            "team": _team_to_api_model(team, members=members, current_user_id=current_user_id)
        },
    }


@router.get(
    "/{teamId}/members",
    summary="Enumerate Team Members",
)
async def get_team_members(
    team_id: Annotated[int, Path(ge=1, alias="teamId")],
    queryRealNameStatus: bool = Query(default=False),
    service: TeamService = Depends(get_team_service),
) -> dict:
    """Return team members for a given team.

    NOTE: This implementation currently does not join real user info or real-name
    verification status. It focuses on matching the response shape.
    """
    _ = queryRealNameStatus  # Placeholder, real implementation will use this flag
    relations = await service.get_team_members(team_id=team_id)
    members = [_member_to_api_model(rel) for rel in relations]
    # allMembersVerified will be None until real-name logic is wired in
    return {
        "code": 200,
        "message": "OK",
        "data": {
            "members": members,
            "allMembersVerified": None,
        },
    }


@router.post(
    "",
    summary="Create Team",
    status_code=status.HTTP_201_CREATED,
)
async def create_team(
    payload: dict,
    auth_user: AuthUserInfo = Depends(get_auth_user),
    service: TeamService = Depends(get_team_service),
) -> dict:
    name = payload.get("name")
    intro = payload.get("intro")
    description = payload.get("description")
    if intro is None:
        intro = ""
    elif not isinstance(intro, str):
        raise BadRequestError("intro must be string")
    if description is None:
        description = ""
    elif not isinstance(description, str):
        raise BadRequestError("description must be string")
    avatar_id = payload.get("avatarId", 1)
    if not isinstance(name, str) or not name.strip():
        raise BadRequestError("name is required")
    if not isinstance(avatar_id, int) or avatar_id <= 0:
        avatar_id = 1

    team = await service.create_team(
        name=name,
        intro=intro,
        description=description,
        avatar_id=avatar_id,
        owner_id=auth_user.user_id,
    )
    members = list(await service.get_team_members(team_id=team.id))
    return {
        "code": 201,
        "message": "Team created",
        "data": {
            "team": _team_to_api_model(team, members=members, current_user_id=auth_user.user_id)
        },
    }


@router.patch(
    "/{teamId}",
    summary="Update Team",
)
async def patch_team(
    team_id: Annotated[int, Path(ge=1, alias="teamId")],
    payload: dict,
    auth_user: AuthUserInfo = require_permission(Action.UPDATE, Resource.TEAM, "teamId"),
    service: TeamService = Depends(get_team_service),
) -> dict:
    intro = payload.get("intro")
    description = payload.get("description")
    name = payload.get("name")
    if intro is not None and not isinstance(intro, str):
        raise BadRequestError("intro must be string")
    if description is not None and not isinstance(description, str):
        raise BadRequestError("description must be string")
    if name is not None and not isinstance(name, str):
        raise BadRequestError("name must be string")
    team = await service.update_team(
        team_id=team_id,
        actor_user_id=auth_user.user_id,
        name=name,
        intro=intro,
        description=description,
        avatar_id=payload.get("avatarId"),
    )
    members = list(await service.get_team_members(team_id=team_id))
    return {
        "code": 200,
        "message": "OK",
        "data": {
            "team": _team_to_api_model(team, members=members, current_user_id=auth_user.user_id)
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
    auth_user: AuthUserInfo = Depends(get_auth_user),
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
    payload: dict,
    auth_user: AuthUserInfo = require_permission(Action.UPDATE, Resource.TEAM_MEMBERSHIP, "teamId"),
    service: TeamService = Depends(get_team_service),
) -> dict:
    role_value = payload.get("role")
    if not isinstance(role_value, str):
        raise BadRequestError("role is required")
    mapped_role = _parse_role(role_value, allow_owner=False)

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
    return {
        "code": 200,
        "message": "OK",
        "data": {
            "team": _team_to_api_model(team, members=members, current_user_id=auth_user.user_id)
        },
    }


@router.post(
    "/{teamId}/members",
    summary="Add Team Member",
    status_code=status.HTTP_201_CREATED,
)
async def add_team_member_entry(
    team_id: Annotated[int, Path(ge=1, alias="teamId")],
    payload: dict,
    auth_user: AuthUserInfo = require_permission(Action.CREATE, Resource.TEAM_MEMBERSHIP, "teamId"),
    service: TeamService = Depends(get_team_service),
) -> dict:
    _ = auth_user
    user_id = payload.get("userId")
    role_str = (payload.get("role") or "MEMBER").upper()
    if not isinstance(user_id, int) or user_id <= 0:
        raise BadRequestError("userId must be positive")

    role_map = {"MEMBER": TeamMemberRole.MEMBER, "ADMIN": TeamMemberRole.ADMIN}
    role_val = role_map.get(role_str)
    if role_val is None:
        raise BadRequestError(f"Invalid role: {role_str}. Must be MEMBER or ADMIN")

    team = await service.get_team(team_id)
    if team is None:
        raise NotFoundError("Team not found")

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
    items = [_application_to_api_model(app) for app in apps]
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
) -> dict:
    return await list_team_join_requests(
        team_id=team_id,
        status=status,
        pageStart=pageStart,
        pageSize=pageSize,
        auth_user=auth_user,
        membership_service=membership_service,
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
    items = [_application_to_api_model(app) for app in apps]
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
    payload: dict,
    auth_user: AuthUserInfo = require_permission(Action.CREATE, Resource.TEAM_INVITATION, "teamId"),
    membership_service: TeamMembershipService = Depends(get_team_membership_service),
) -> dict:
    user_id = payload.get("userId")
    if not isinstance(user_id, int) or user_id <= 0:
        raise BadRequestError("userId must be a positive integer")
    role_value = payload.get("role") if isinstance(payload.get("role"), str) else None
    role = _parse_role(role_value, allow_owner=False)
    message = payload.get("message")
    if message is not None and not isinstance(message, str):
        raise BadRequestError("message must be string")
    app = await membership_service.create_team_invitation(
        initiator_user_id=auth_user.user_id,
        team_id=team_id,
        user_id_to_invite=user_id,
        role=role,
        message=message,
    )
    return {
        "code": 201,
        "message": "Invitation created",
        "data": {"invitation": _application_to_api_model(app)},
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
    payload: dict,
    auth_user: AuthUserInfo = Depends(get_auth_user),
    membership_service: TeamMembershipService = Depends(get_team_membership_service),
) -> dict:
    message = payload.get("message")
    if message is not None and not isinstance(message, str):
        raise BadRequestError("message must be string")
    app = await membership_service.create_team_join_request(
        user_id=auth_user.user_id,
        team_id=team_id,
        message=message,
    )
    return {
        "code": 201,
        "message": "Join request created",
        "data": {"application": _application_to_api_model(app)},
    }


@router.post(
    "/{teamId}/requests",
    summary="Create Team Request",
    status_code=status.HTTP_201_CREATED,
)
async def create_team_request_alias(
    team_id: Annotated[int, Path(ge=1, alias="teamId")],
    payload: dict,
    auth_user: AuthUserInfo = Depends(get_auth_user),
    membership_service: TeamMembershipService = Depends(get_team_membership_service),
) -> dict:
    return await create_team_join_request_via_team(
        team_id=team_id,
        payload=payload,
        auth_user=auth_user,
        membership_service=membership_service,
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
