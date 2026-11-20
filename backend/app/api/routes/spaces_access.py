"""Whether a space is members-only or open to an email-suffix scope.

``PUT /spaces/{spaceId}/access`` is the settings page's one write for it. Going
back to members-only, or narrowing an open space's scope, applies at once;
opening a space or widening its scope files a request the platform reviews
(``app.domain.space.access_service``), and the reply's ``openRequest`` says it
is waiting. The module mounts itself through ``app.main._discover_routers``.
"""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Path
from pydantic import BaseModel, ConfigDict, Field

from app.api.response import ok, typed_response
from app.api.space_deps import get_space_service, require_reviewed_space
from app.api.space_payloads import _build_full_space_payload
from app.api.write_access import Can
from app.auth.checker import require_auth_user
from app.auth.core import AuthUserInfo
from app.core.errors import BadRequestError
from app.core.sentences import say
from app.db.session import get_db
from app.domain.space.access_service import SpaceAccessService
from app.domain.space.permissions import Permission as SpacePermission
from app.domain.space.services import SpaceService

router = APIRouter(
    prefix="/spaces", tags=["Spaces"], dependencies=[Depends(require_reviewed_space)]
)


class SpaceAccessRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    #: ``members``: only managers and members read the space. ``open``:
    #: everyone signed in whose email is in ``scope`` does too.
    mode: Literal["members", "open"]
    #: Registered-email suffixes, such as ``ruc.edu.cn``; required for ``open``.
    scope: list[str] = Field(default_factory=list)


class SpaceOpenRequestView(BaseModel):
    """The space's latest request to open, as its settings page shows it."""

    id: int
    scope: list[str]
    status: Literal["PENDING", "APPROVED", "REJECTED"]
    reason: str | None
    createdAt: str
    reviewedAt: str | None


class SpaceAccessView(BaseModel):
    """The fields of the space payload this route is about; the reply carries
    the whole space payload (``_build_full_space_payload``)."""

    model_config = ConfigDict(extra="allow")

    id: int
    openScope: list[str] | None
    openRequest: SpaceOpenRequestView | None


class SpaceAccessReply(BaseModel):
    space: SpaceAccessView


@router.put(
    "/{spaceId}/access",
    summary="Set who reads a space",
    dependencies=[Can(SpacePermission.edit_settings)],
    **typed_response(SpaceAccessReply),
)
async def put_space_access(
    space_id: Annotated[int, Path(ge=1, alias="spaceId")],
    payload: SpaceAccessRequest,
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: SpaceService = Depends(get_space_service),
    db=Depends(get_db),
) -> dict:
    access = SpaceAccessService(db)
    if payload.mode == "members":
        if payload.scope:
            raise BadRequestError(say("openSpaceMembersNoScope"))
        space = await access.set_members_only(space_id)
    else:
        space = await access.open(
            space_id, scope=payload.scope, requested_by=auth_user.user_id
        )
    # Commit before answering: a narrowed space is narrowed for the next
    # request anyone makes, and a filed request is in the reviewers' queue.
    # Same reason as the commit in ``create_space``.
    await db.commit()
    space_data = await _build_full_space_payload(
        space, service=service, db=db, viewer_id=auth_user.user_id, include_private=True
    )
    return ok({"space": space_data})
