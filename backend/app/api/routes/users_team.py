"""My team membership: the requests I sent, the invitations I got, leaving.

Third slice of `app/api/routes/users.py` (arch review C-backend.md §3.3), after
`users_sessions.py` and `users_password.py`. The six `/users/me/team*` routes
are one concept -- "where do I stand with a team, and how do I get out of one":

- `DELETE /users/me/team-requests/{requestId}` -> `cancel_my_join_request`
- `DELETE /users/me/teams/{teamId}` -> `leave_team`
- `POST /users/me/team-invitations/{invitationId}/accept`
  -> `accept_team_invitation`
- `POST /users/me/team-invitations/{invitationId}/decline`
  -> `decline_team_invitation`
- `GET /users/me/team-requests` -> `list_my_team_requests`
- `GET /users/me/team-invitations` -> `list_my_team_invitations`

They go through `TeamMembershipService` and `TeamService` only, so the block
moves as a whole, together with the `get_team_membership_service` dependency
that nothing else in `users.py` used.

Two things stay behind, and why.

`parse_application_status` lives in `app/domain/team/vocabulary.py`, which
this module imports it from. It turns the `?status=` query into the
domain's `ApplicationStatus`, and a route module may not import that model
directly (`routes-touch-no-models` in `.importlinter`), so the domain
parses its own vocabulary.

The application-to-JSON shape stays in `routes/teams.py`, which grows a small
public facade for it: `application_to_api_model` and `load_application_maps` are
what the team-facing routes there and these personal ones both answer with, so
the shape keeps one home instead of a copy per caller. That adds nothing to a
boundary -- both callers are under `app.api.routes` (C1), and the two helpers
touch repositories, never models (C2).

Ordering. This module sorts after `users.py` (`.` < `_`), so its router mounts
after that file's, and the six paths keep their relative order among themselves.
Nothing earlier can shadow them: each is the only full match for its URL in the
whole route table, and `GET /users/{userId}` takes one segment where these have
one or two literal ones.

The new module mounts itself: `app.main._discover_routers` includes every
module-level `APIRouter` under `app.api.routes`, so the same
`APIRouter(prefix="/users", tags=["Users"])` is all it takes.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Response, status

from app.api.routes.teams import application_to_api_model, load_application_maps
from app.auth.checker import require_auth_user
from app.auth.core import AuthUserInfo
from app.db.session import get_db
from app.domain.team.membership_services import TeamMembershipService
from app.domain.team.repositories import (
    TeamMembershipApplicationRepository,
    TeamRepository,
)
from app.domain.team.services import TeamService
from app.domain.team.vocabulary import parse_application_status

router = APIRouter(prefix="/users", tags=["Users"])


async def get_team_membership_service(
    db=Depends(get_db),
) -> TeamMembershipService:
    team_repo = TeamRepository(session=db)
    app_repo = TeamMembershipApplicationRepository(session=db)
    return TeamMembershipService(
        session=db, team_repo=team_repo, application_repo=app_repo
    )


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
    pageStart: int | None = Query(default=None, ge=0),
    pageSize: int | None = Query(default=None, ge=1, le=100),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    membership_service: TeamMembershipService = Depends(get_team_membership_service),
    db=Depends(get_db),
) -> dict:
    status_enum = parse_application_status(status)
    apps, page = await membership_service.list_my_join_requests(
        user_id=auth_user.user_id,
        status=status_enum,
        page_start=pageStart,
        page_size=pageSize,
    )
    users_map, profiles_map, avatars_map, teams_map = await load_application_maps(
        db, apps
    )
    items = [
        application_to_api_model(
            app,
            users_map=users_map,
            profiles_map=profiles_map,
            avatars_map=avatars_map,
            teams_map=teams_map,
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
    pageStart: int | None = Query(default=None, ge=0),
    pageSize: int | None = Query(default=None, ge=1, le=100),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    membership_service: TeamMembershipService = Depends(get_team_membership_service),
    db=Depends(get_db),
) -> dict:
    status_enum = parse_application_status(status)
    apps, page = await membership_service.list_my_invitations(
        user_id=auth_user.user_id,
        status=status_enum,
        page_start=pageStart,
        page_size=pageSize,
    )
    users_map, profiles_map, avatars_map, teams_map = await load_application_maps(
        db, apps
    )
    items = [
        application_to_api_model(
            app,
            users_map=users_map,
            profiles_map=profiles_map,
            avatars_map=avatars_map,
            teams_map=teams_map,
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
