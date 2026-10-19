"""Closing your own account (注销), and which handles belong to closed accounts.

Closing is the person's own decision, made once, behind the same
re-authentication as every other operation that cannot be taken back
(``SudoPurpose.ACCOUNT_DELETE``), and after typing their own username so a
stray click cannot do it. What goes and what stays is
``app.domain.account.deletion``.
"""

from typing import Annotated

from fastapi import APIRouter, Body, Query, Response
from pydantic import BaseModel, ConfigDict, Field

from app.api.request_deps import AuthUser, DbSession
from app.api.response import ok, typed_response
from app.api.users_deps import _clear_refresh_cookie, _spend_sudo_ticket
from app.api.write_access import ROUTE_DECIDES
from app.common.auth import SudoPurpose
from app.core.deleted_accounts import mark_deleted
from app.core.errors import NotFoundError, UnprocessableEntityError
from app.core.sentences import say
from app.domain.account.deletion import close_account, closed_handles
from app.domain.user.services import user_by_id

router = APIRouter(tags=["Users"], dependencies=[ROUTE_DECIDES])


#: How many handles one lookup may ask about: a window of a room's timeline.
_MAX_HANDLES = 200


class AccountClosedOut(BaseModel):
    closed: bool


class ClosedHandlesOut(BaseModel):
    handles: list[str]


class CloseAccountIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    sudo_ticket: str | None = Field(default=None, alias="sudoTicket")
    username: str = ""


@router.delete("/users/me", **typed_response(AccountClosedOut))
async def close_my_account(
    response: Response,
    db: DbSession,
    auth_user: AuthUser,
    payload: CloseAccountIn = Body(default_factory=CloseAccountIn),
) -> dict:
    """Close the caller's account. Nothing is closed until both the typed
    username and the re-authentication check out."""
    user = await user_by_id(db, auth_user.user_id)
    if user is None:
        raise NotFoundError(say("accountAlreadyClosed"))
    if payload.username.strip().lower() != user.username.lower():
        raise UnprocessableEntityError(say("accountCloseUsernameMismatch"))
    await _spend_sudo_ticket(
        payload.sudo_ticket,
        user_id=auth_user.user_id,
        purpose=SudoPurpose.ACCOUNT_DELETE,
    )
    closed = await close_account(db, auth_user.user_id)
    if closed is None:
        raise NotFoundError(say("accountAlreadyClosed"))
    await db.commit()
    await mark_deleted(closed.user_id)
    _clear_refresh_cookie(response)
    return ok({"closed": True})


# Not under ``/users``: ``GET /users/{userId}`` would take the second segment.
@router.get("/closed-accounts", **typed_response(ClosedHandlesOut))
async def which_are_closed(
    db: DbSession,
    _auth_user: AuthUser,
    handles: Annotated[str, Query(max_length=8000)] = "",
) -> dict:
    """Of the comma-separated ``handles``, the ones whose account is closed,
    so a screen can show their old messages as 「已注销用户」 rather than a
    bare handle."""
    wanted = [h.strip() for h in handles.split(",") if h.strip()][:_MAX_HANDLES]
    return ok({"handles": await closed_handles(db, wanted)})
