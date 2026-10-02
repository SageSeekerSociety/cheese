"""The UI language a person picked, kept on their account.

Their push and desktop notifications are written in it on the server
(`app/domain/notification/push.py`): Web Push hands the browser finished text,
so the server has to know which language each recipient reads. The page saves
the choice here whenever the person switches, and adopts it wherever they sign
in (`frontend/src/services/account.ts`); the owner's user record carries it
(`UserAuthService.build_user_dto`).

Ordering. This module sorts after the `users` package, so its router mounts
after that package's. `PUT /users/me/language` has two segments under `/users`,
and no earlier PUT route has that shape, so nothing registered before it can
shadow it.
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.checker import require_auth_user
from app.auth.core import AuthUserInfo
from app.core.errors import BadRequestError, NotFoundError
from app.db.session import get_db
from app.domain.block.notice_text import LOCALES
from app.domain.user.services import set_language

router = APIRouter(prefix="/users", tags=["Users"])


class LanguageRequest(BaseModel):
    language: str


@router.put(
    "/me/language",
    summary="Save the UI language the signed-in user picked",
)
async def set_my_language(
    payload: LanguageRequest,
    auth_user: AuthUserInfo = Depends(require_auth_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    if payload.language not in LOCALES:
        raise BadRequestError(f"Unsupported language: {payload.language}")
    if not await set_language(session, auth_user.user_id, payload.language):
        raise NotFoundError("User not found")
    return {
        "code": 200,
        "message": "Language saved.",
        "data": {"language": payload.language},
    }
