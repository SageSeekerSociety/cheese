"""The account's real name: read it, set it, correct it, delete it, audit it.

Fourth slice of `app/api/routes/users.py` (arch review C-backend.md §3.3).
users.py is 4,282 lines against a 1,500-line cap that only ratchets down. The
block that moves is the one concept "the real-name identity attached to this
account", as five routes:

  GET    /users/{userId}/identity              (masked, or precise under sudo)
  PUT    /users/{userId}/identity
  PATCH  /users/{userId}/identity
  DELETE /users/{userId}/identity
  GET    /users/{userId}/identity/access-logs

and the two shapes they read, `PutUserIdentityRequest` and
`PatchUserIdentityRequest`: nothing else in the tree names either, so they are
this module's own request models rather than two more classes at the top of
the file being split. SudoTicketRequest is shared with passkey and two-factor
operations, so it is imported from users_common beside the ticket spender.

The three writes and the precise read each spend a sudo ticket; that is the
decision this slice had to make once for all three of its modules, and it is
recorded on `users_common.py`, which is where `_spend_sudo_ticket` now lives.
`get_user_realname_service` is a FastAPI dependency assembled in `app/api/deps.py`,
alongside the other account service dependencies.

Ordering. This module sorts after `users.py` and `users_2fa.py`, and before
`users_passkey.py`, so its paths mount after every path that stays. A moved
path is shadowed only if a route registered earlier matches it with a
parameter where it carries a literal; all five carry `identity` in the segment
after `{userId}`, and no route in the table — before or after this move — puts
a parameter there. Resolving every path in the table confirms each of the five
still reaches the handler it did before, now under
`app.api.routes.users_identity`. OpenAPI is byte-identical apart from the five
paths' position in the paths object.

The new module mounts itself: `app.main._discover_routers` includes every
module-level `APIRouter` under `app.api.routes`, so the declaration below, with
`APIRouter(prefix="/users", tags=["Users"])`, is all it takes.
"""

from typing import Annotated

from fastapi import APIRouter, Body, Depends, Path, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import get_user_realname_service
from app.api.routes.users_common import SudoTicketRequest, _spend_sudo_ticket
from app.auth.checker import require_auth_user
from app.auth.core import AuthUserInfo
from app.common.auth import SudoPurpose
from app.core.client_address import resolved_client_address
from app.core.errors import ForbiddenError, NotFoundError
from app.domain.user.realname_services import UserRealNameService


class PutUserIdentityRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    real_name: str = Field(default="", alias="realName")
    student_id: str = Field(default="", alias="studentId")
    grade: str = ""
    major: str = ""
    class_name: str = Field(default="", alias="className")
    sudo_ticket: str | None = Field(default=None, alias="sudoTicket")


class PatchUserIdentityRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    real_name: str | None = Field(default=None, alias="realName")
    student_id: str | None = Field(default=None, alias="studentId")
    grade: str | None = None
    major: str | None = None
    class_name: str | None = Field(default=None, alias="className")
    sudo_ticket: str | None = Field(default=None, alias="sudoTicket")


router = APIRouter(prefix="/users", tags=["Users"])


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
    sudo_ticket: str | None = Query(default=None, alias="sudoTicket"),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    realname_service: UserRealNameService = Depends(get_user_realname_service),
) -> dict:
    """The owner's identity, masked unless ``precise`` is asked for.

    Nobody else reads it in either form: the masked one still carries grade,
    major and class in full, which with a surname names a student. The check
    comes before the lookup, so a refusal says nothing about whether a record
    exists.

    The unmasked name and student ID take a fresh re-authentication: a
    session alone, stolen or left open, only ever reads the masked form.
    """

    if auth_user.user_id != user_id:
        raise ForbiddenError("Only the user themselves can view identity.")
    if precise:
        await _spend_sudo_ticket(
            sudo_ticket, user_id=auth_user.user_id, purpose=SudoPurpose.REALNAME_VIEW
        )

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
                ip_address=resolved_client_address(request) or "",
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
    await _spend_sudo_ticket(
        payload.sudo_ticket,
        user_id=auth_user.user_id,
        purpose=SudoPurpose.REALNAME_UPDATE,
    )
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
    await _spend_sudo_ticket(
        payload.sudo_ticket,
        user_id=auth_user.user_id,
        purpose=SudoPurpose.REALNAME_UPDATE,
    )
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


@router.delete(
    "/{userId}/identity",
    summary="Delete User Real Name Identity Info",
)
async def delete_user_identity(
    user_id: Annotated[int, Path(ge=1, alias="userId")],
    payload: SudoTicketRequest = Body(default_factory=SudoTicketRequest),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    realname_service: UserRealNameService = Depends(get_user_realname_service),
) -> dict:
    """The owner removes their record. Who read it before stays on record."""
    if auth_user.user_id != user_id:
        raise ForbiddenError("Only the user themselves can delete identity.")
    await _spend_sudo_ticket(
        payload.sudo_ticket,
        user_id=auth_user.user_id,
        purpose=SudoPurpose.REALNAME_DELETE,
    )
    await realname_service.delete_user_identity(user_id)
    return {"code": 200, "message": "Success"}


@router.get(
    "/{userId}/identity/access-logs",
    summary="Get User Real Name Identity Access Logs",
)
async def get_user_identity_access_logs(
    user_id: Annotated[int, Path(ge=1, alias="userId")],
    pageStart: int | None = Query(default=None, ge=0),
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
