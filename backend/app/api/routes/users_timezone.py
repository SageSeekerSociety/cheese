"""The time zone of the browser a person last used, kept on their account.

Quiet hours are wall-clock hours (22:00 to 08:00), so the server needs to know
whose wall clock: `notification.outbox` reads each recipient's on it. The page
reports the browser's zone whenever it loads the signed-in user and the two
differ (`frontend/src/services/account.ts`), so it follows the person when they
travel; the owner's user record carries it (`UserAuthService.build_user_dto`).

Ordering. This module sorts after the `users` package, so its router mounts
after that package's. `PUT /users/me/timezone` has two segments under `/users`,
and no earlier PUT route has that shape, so nothing registered before it can
shadow it.
"""

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.checker import require_auth_user
from app.auth.core import AuthUserInfo
from app.core.errors import BadRequestError, NotFoundError
from app.db.session import get_db
from app.domain.user.services import set_timezone

router = APIRouter(prefix="/users", tags=["Users"])


class TimezoneRequest(BaseModel):
    timezone: str


@router.put(
    "/me/timezone",
    summary="Save the time zone of the signed-in user's browser",
)
async def set_my_timezone(
    payload: TimezoneRequest,
    auth_user: AuthUserInfo = Depends(require_auth_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    try:
        ZoneInfo(payload.timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise BadRequestError(f"Unknown time zone: {payload.timezone}") from exc
    if not await set_timezone(session, auth_user.user_id, payload.timezone):
        raise NotFoundError("User not found")
    return {
        "code": 200,
        "message": "Time zone saved.",
        "data": {"timezone": payload.timezone},
    }
