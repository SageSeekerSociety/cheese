"""Closing an account for its owner, when they asked by email.

The privacy policy lets a person ask ops@okcheese.com to close their account.
Whoever answers that mail closes it here, after checking the request came from
the account's own address; the effect is the same as the person closing it
themselves (``app.domain.account.deletion``).
"""

from fastapi import APIRouter
from pydantic import BaseModel

from app.api.admin_deps import PlatformAdminDep
from app.api.request_deps import DbSession
from app.api.response import ok, typed_response
from app.api.write_access import ROUTE_DECIDES
from app.core.deleted_accounts import mark_deleted
from app.core.errors import ForbiddenError, NotFoundError
from app.core.sentences import say
from app.domain.account.deletion import close_account
from app.domain.user.services import user_by_handle


class AccountClosedForOwnerOut(BaseModel):
    closed: bool
    handle: str


router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[ROUTE_DECIDES])


@router.delete("/users/{handle}", **typed_response(AccountClosedForOwnerOut))
async def close_account_for_owner(
    handle: str, db: DbSession, admin: PlatformAdminDep
) -> dict:
    if handle.lower() == admin.lower():
        raise ForbiddenError(say("accountCloseNotOwnViaAdmin"))
    user = await user_by_handle(db, handle)
    if user is None:
        raise NotFoundError(say("accountNotFound", handle=handle))
    closed = await close_account(db, user.id)
    if closed is None:
        raise NotFoundError(say("accountNotFound", handle=handle))
    await db.commit()
    await mark_deleted(closed.user_id)
    return ok({"closed": True, "handle": closed.handle})
