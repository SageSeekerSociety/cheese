"""用户已绑定的第三方账号，列出与解绑。"""

from typing import TYPE_CHECKING, Annotated

from fastapi import (
    APIRouter,
    Body,
    Depends,
    Path,
)

from app.api.deps import (
    get_oauth_service,
)
from app.api.routes.users_common import (
    SudoTicketRequest,
    _spend_sudo_ticket,
)
from app.auth.checker import require_auth_user
from app.auth.core import AuthUserInfo
from app.common.auth import (
    SudoPurpose,
)
from app.core.errors import (
    ForbiddenError,
    NotFoundError,
)
from app.domain.oauth.services import OAuthService

if TYPE_CHECKING:
    pass

router = APIRouter(prefix="/users", tags=["Users"])


@router.get(
    "/{userId}/oauth/connections",
    summary="List user OAuth connections",
)
async def list_oauth_connections(
    user_id: Annotated[int, Path(ge=0, alias="userId")],
    auth_user: AuthUserInfo = Depends(require_auth_user),
    oauth_service: OAuthService = Depends(get_oauth_service),
) -> dict:
    if auth_user.user_id != user_id:
        raise ForbiddenError(
            "Only the user themselves can view their OAuth connections."
        )

    connections = await oauth_service.list_user_connections(user_id)

    return {
        "code": 200,
        "message": "OK",
        "data": {"connections": connections},
    }


@router.delete(
    "/{userId}/oauth/connections/{connectionId}",
    summary="Unbind OAuth connection",
)
async def delete_oauth_connection(
    user_id: Annotated[int, Path(ge=0, alias="userId")],
    connection_id: Annotated[int, Path(alias="connectionId")],
    payload: SudoTicketRequest = Body(default_factory=SudoTicketRequest),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    oauth_service: OAuthService = Depends(get_oauth_service),
) -> dict:
    if auth_user.user_id != user_id:
        raise ForbiddenError(
            "Only the user themselves can unbind their OAuth connections."
        )

    await _spend_sudo_ticket(
        payload.sudo_ticket,
        user_id=auth_user.user_id,
        purpose=SudoPurpose.OAUTH_UNBIND,
    )

    deleted = await oauth_service.delete_connection(connection_id, user_id)

    if not deleted:
        raise NotFoundError("OAuth connection not found")

    return {
        "code": 200,
        "message": "OAuth connection removed successfully.",
    }
