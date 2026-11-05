"""A space's join link, and the requests it produces.

Two doors, the same shape a team's link has (``routes/teams.py``):

* ``/spaces/{spaceId}/join-link`` and ``/spaces/{spaceId}/join-requests`` are
  the owner's and admins' side: read the link, reset it, switch approval on or
  off, and answer the requests waiting while approval is on.
* ``/space-invites/{token}`` is the holder's side: read which space the link
  names and where the reader stands, then join it (or ask to). It sits outside
  ``/spaces/{spaceId}`` because the holder is not yet anyone the space shows
  itself to; a reset link, a deleted space and one the platform has not
  approved all answer 404, like a space the reader may not see.
"""

from typing import Annotated, Any, Literal, Protocol

from fastapi import APIRouter, Depends, Path, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Mapped

from app.api.response import ok, typed_response
from app.api.space_deps import get_space_service, require_reviewed_space
from app.api.space_payloads import people_by_ids
from app.api.write_access import HOLDS_LINK, Can
from app.auth.checker import require_auth_user
from app.auth.core import AuthUserInfo
from app.db.session import get_db
from app.domain.space.permissions import Permission as SpacePermission
from app.domain.space.services import SpaceService

router = APIRouter(
    prefix="/spaces", tags=["Spaces"], dependencies=[Depends(require_reviewed_space)]
)
invites_router = APIRouter(prefix="/space-invites", tags=["Spaces"])


class _LinkedSpace(Protocol):
    id: Mapped[int]
    name: Mapped[str]
    intro: Mapped[str]
    avatar_id: Mapped[int | None]
    join_token: Mapped[str | None]
    join_approval: Mapped[bool]


class JoinLinkSettings(BaseModel):
    approval: bool


class SpaceJoinLink(BaseModel):
    """The link as its managers see it: the token the URL carries, and whether
    joining through it waits for approval."""

    token: str
    approval: bool


class SpaceJoinRequestRow(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: int
    user_id: int = Field(alias="userId")
    #: The same hydrated person a member row carries.
    user: dict[str, Any] | None
    created_at: int = Field(alias="createdAt")


class SpaceJoinRequests(BaseModel):
    requests: list[SpaceJoinRequestRow]


class SpaceInviteSpace(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: int
    name: str
    intro: str
    avatar_id: int | None = Field(alias="avatarId")


class SpaceInvite(BaseModel):
    """What a link's holder is shown: which space, how joining works, and where
    they stand."""

    model_config = ConfigDict(populate_by_name=True)

    space: SpaceInviteSpace
    join_approval: bool = Field(alias="joinApproval")
    join_status: Literal["member", "pending", "none"] = Field(alias="joinStatus")


def _join_link_payload(space: _LinkedSpace) -> dict:
    return ok({"token": space.join_token, "approval": space.join_approval})


@router.get(
    "/{spaceId}/join-link",
    summary="Get Space Join Link",
    **typed_response(SpaceJoinLink),
    dependencies=[Can(SpacePermission.manage_members)],
)
async def get_space_join_link(
    space_id: Annotated[int, Path(ge=1, alias="spaceId")],
    service: SpaceService = Depends(get_space_service),
    auth_user: AuthUserInfo = Depends(require_auth_user),
) -> dict:
    return _join_link_payload(await service.join_link(space_id=space_id))


@router.patch(
    "/{spaceId}/join-link",
    summary="Update Space Join Link",
    **typed_response(SpaceJoinLink),
    dependencies=[Can(SpacePermission.manage_members)],
)
async def update_space_join_link(
    space_id: Annotated[int, Path(ge=1, alias="spaceId")],
    payload: JoinLinkSettings,
    service: SpaceService = Depends(get_space_service),
    auth_user: AuthUserInfo = Depends(require_auth_user),
) -> dict:
    space = await service.join_link(space_id=space_id, approval=payload.approval)
    return _join_link_payload(space)


@router.post(
    "/{spaceId}/join-link/reset",
    summary="Reset Space Join Link",
    **typed_response(SpaceJoinLink),
    dependencies=[Can(SpacePermission.manage_members)],
)
async def reset_space_join_link(
    space_id: Annotated[int, Path(ge=1, alias="spaceId")],
    service: SpaceService = Depends(get_space_service),
    auth_user: AuthUserInfo = Depends(require_auth_user),
) -> dict:
    return _join_link_payload(await service.join_link(space_id=space_id, reset=True))


@router.get(
    "/{spaceId}/join-requests",
    summary="List Space Join Requests",
    **typed_response(SpaceJoinRequests),
    dependencies=[Can(SpacePermission.manage_members)],
)
async def list_space_join_requests(
    space_id: Annotated[int, Path(ge=1, alias="spaceId")],
    service: SpaceService = Depends(get_space_service),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    db=Depends(get_db),
) -> dict:
    requests = await service.list_join_requests(space_id=space_id)
    people = await people_by_ids([r.user_id for r in requests], db=db)
    return ok(
        {
            "requests": [
                {
                    "id": r.id,
                    "userId": r.user_id,
                    "user": people[r.user_id],
                    "createdAt": int(r.created_at.timestamp() * 1000),
                }
                for r in requests
            ]
        }
    )


@router.post(
    "/{spaceId}/join-requests/{requestId}/approve",
    summary="Approve Space Join Request",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Can(SpacePermission.manage_members)],
)
async def approve_space_join_request(
    space_id: Annotated[int, Path(ge=1, alias="spaceId")],
    request_id: Annotated[int, Path(ge=1, alias="requestId")],
    service: SpaceService = Depends(get_space_service),
    auth_user: AuthUserInfo = Depends(require_auth_user),
) -> Response:
    await service.decide_join_request(
        space_id=space_id,
        request_id=request_id,
        approve=True,
        actor_user_id=auth_user.user_id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{spaceId}/join-requests/{requestId}/reject",
    summary="Reject Space Join Request",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Can(SpacePermission.manage_members)],
)
async def reject_space_join_request(
    space_id: Annotated[int, Path(ge=1, alias="spaceId")],
    request_id: Annotated[int, Path(ge=1, alias="requestId")],
    service: SpaceService = Depends(get_space_service),
    auth_user: AuthUserInfo = Depends(require_auth_user),
) -> Response:
    await service.decide_join_request(
        space_id=space_id,
        request_id=request_id,
        approve=False,
        actor_user_id=auth_user.user_id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _invite_payload(space: _LinkedSpace, join_status: str) -> dict:
    """What the join page shows: which space, how joining works, and where the
    reader stands (``member``, ``pending`` or ``none``). Only what a holder of
    the link may know about a space they are not in."""
    return ok(
        {
            "space": {
                "id": space.id,
                "name": space.name,
                "intro": space.intro,
                "avatarId": space.avatar_id,
            },
            "joinApproval": space.join_approval,
            "joinStatus": join_status,
        }
    )


@invites_router.get(
    "/{token}",
    summary="Read Space Through Its Join Link",
    **typed_response(SpaceInvite),
)
async def get_space_by_join_link(
    token: str,
    service: SpaceService = Depends(get_space_service),
    auth_user: AuthUserInfo = Depends(require_auth_user),
) -> dict:
    space = await service.space_for_join_token(token)
    join_status = await service.join_status(
        space_id=space.id, user_id=auth_user.user_id
    )
    return _invite_payload(space, join_status)


@invites_router.post(
    "/{token}/join",
    summary="Join Space Through Its Join Link",
    **typed_response(SpaceInvite),
    dependencies=[HOLDS_LINK],
)
async def join_space_by_join_link(
    token: str,
    service: SpaceService = Depends(get_space_service),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    db=Depends(get_db),
) -> dict:
    space, join_status = await service.join_by_link(
        token=token, user_id=auth_user.user_id
    )
    payload = _invite_payload(space, join_status)
    # Commit before answering: someone told they have joined opens the space on
    # their next request, and the space is closed to non-members.
    await db.commit()
    return payload
