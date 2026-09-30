"""The invite codes of a space: the links that let somebody in.

A slice of `app/api/routes/spaces.py` (arch review, tracking issue #2143): the
four `/{spaceId}/invite-codes` routes -- list, create, update, revoke -- one
concept. Each is one view of the same table: which codes this board is handing
out, who made each one, how many uses are left and when one stops working. All
four go through ``SpaceService`` and hand their row back in the one shape the
邀请码 screen reads, its maker hydrated, so the list and the create response
cannot drift into two spellings of one row.

The module declares its own ``APIRouter`` with the same prefix, tags and
``require_reviewed_space`` dependency and mounts itself through
``app.main._discover_routers``, like every other route module.

This block is *not* the tail of `spaces.py`'s route list, so unlike the earlier
slices (#2198, #2204, #2208) the move shifts path order: the legacy NT-API rows
that still stand after it -- `GET /{spaceId}/categories`, `GET
/{spaceId}/analytics/tasks` and the two deprecated analytics exports -- mount
before this module, because ``spaces.py`` < ``spaces_analytics.py`` <
``spaces_invite_codes.py`` < ``spaces_member.py``. Every path, method and
``operationId`` is unchanged and no route's first full match moves; the two
invite-code paths only change their index in the openapi ``paths`` map. The one
adjacent
category route, `GET /{spaceId}/categories`, goes instead to
`spaces_organization.py`, where the other six category routes already live -- so
the categories stay read in one module rather than following the invite codes
into one named after something else.

The helpers the moved routes share with the routes that stay --
`_invite_codes_to_api_models`, which ``create_space`` reads a new board's first
code back through -- and the two request bodies ``CreateSpaceInviteCodeRequest``
/ ``PatchSpaceInviteCodeRequest`` (defined in `spaces.py`'s shared Request
Models block and named nowhere else) stay in `spaces.py` and are imported here,
the shape `spaces_organization.py` already uses. `spaces.py` imports nothing
from this module, so there is no cycle.

Nothing here reads an `app.domain.*.models` module: the rows arrive only through
`_invite_codes_to_api_models`, whose ``SpaceInviteCode`` annotation lives in
`spaces.py`, so `.importlinter`'s C2 (`routes-touch-no-models`) is untouched and
the frozen baseline does not move.

`UserRepository` / `UserProfileRepository` -- what `_invite_codes_to_api_models`
hydrates a code's maker with -- are imported **from `spaces.py`** rather than
from `app.domain.user.repositories`, the shape `topics_side_routes.py` uses for
`BlockRepository`. The guard in `tests/unit/test_domain_import_guard.py`
ratchets (route module, repository module) pairs, and since a route module
belongs to no domain, a direct import would add a line to that ratchet for a
read `spaces.py` already carries -- splitting the file would have booked a new
debt for an edge that did not change.
"""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Response, status

from app.api.routes.spaces import (
    CreateSpaceInviteCodeRequest,
    PatchSpaceInviteCodeRequest,
    UserProfileRepository,
    UserRepository,
    _invite_codes_to_api_models,
    get_space_service,
    require_reviewed_space,
)
from app.auth.checker import require_auth_user
from app.auth.core import AuthUserInfo
from app.db.session import get_db
from app.domain.space.services import SpaceService

router = APIRouter(
    prefix="/spaces", tags=["Spaces"], dependencies=[Depends(require_reviewed_space)]
)


# ---------------------------------------------------------------------------
# Space Invite Codes (NT-API aligned)
# ---------------------------------------------------------------------------


@router.get(
    "/{spaceId}/invite-codes",
    summary="List Space Invite Codes",
)
async def list_space_invite_codes(
    space_id: Annotated[int, Path(ge=1, alias="spaceId")],
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: SpaceService = Depends(get_space_service),
    db=Depends(get_db),
) -> dict:
    codes = await service.list_invite_codes(
        space_id=space_id, actor_user_id=auth_user.user_id
    )
    items = await _invite_codes_to_api_models(
        codes,
        user_repo=UserRepository(session=db),
        profile_repo=UserProfileRepository(session=db),
    )
    return {"code": 200, "message": "OK", "data": {"inviteCodes": items}}


@router.post(
    "/{spaceId}/invite-codes",
    summary="Create Space Invite Code",
    status_code=status.HTTP_201_CREATED,
)
async def create_space_invite_code(
    space_id: Annotated[int, Path(ge=1, alias="spaceId")],
    payload: CreateSpaceInviteCodeRequest,
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: SpaceService = Depends(get_space_service),
    db=Depends(get_db),
) -> dict:
    expires_at = None
    if payload.expires_at is not None:
        expires_at = datetime.fromtimestamp(payload.expires_at / 1000.0, tz=UTC)
    invite = await service.create_invite_code(
        space_id=space_id,
        actor_user_id=auth_user.user_id,
        max_uses=payload.max_uses,
        expires_at=expires_at,
        note=payload.note,
    )
    items = await _invite_codes_to_api_models(
        [invite],
        user_repo=UserRepository(session=db),
        profile_repo=UserProfileRepository(session=db),
    )
    return {"code": 201, "message": "Created", "data": {"inviteCode": items[0]}}


@router.patch(
    "/{spaceId}/invite-codes/{codeId}",
    summary="Update Space Invite Code",
)
async def patch_space_invite_code(
    space_id: Annotated[int, Path(ge=1, alias="spaceId")],
    code_id: Annotated[int, Path(ge=1, alias="codeId")],
    payload: PatchSpaceInviteCodeRequest,
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: SpaceService = Depends(get_space_service),
    db=Depends(get_db),
) -> dict:
    expires_at = None
    if payload.expires_at is not None:
        expires_at = datetime.fromtimestamp(payload.expires_at / 1000.0, tz=UTC)
    invite = await service.update_invite_code(
        space_id=space_id,
        actor_user_id=auth_user.user_id,
        code_id=code_id,
        max_uses=payload.max_uses,
        max_uses_set="max_uses" in payload.model_fields_set,
        expires_at=expires_at,
        expires_at_set="expires_at" in payload.model_fields_set,
        note=payload.note,
        note_set="note" in payload.model_fields_set,
    )
    items = await _invite_codes_to_api_models(
        [invite],
        user_repo=UserRepository(session=db),
        profile_repo=UserProfileRepository(session=db),
    )
    return {"code": 200, "message": "OK", "data": {"inviteCode": items[0]}}


@router.delete(
    "/{spaceId}/invite-codes/{codeId}",
    summary="Revoke Space Invite Code",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def revoke_space_invite_code(
    space_id: Annotated[int, Path(ge=1, alias="spaceId")],
    code_id: Annotated[int, Path(ge=1, alias="codeId")],
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: SpaceService = Depends(get_space_service),
) -> Response:
    await service.revoke_invite_code(
        space_id=space_id,
        actor_user_id=auth_user.user_id,
        code_id=code_id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
