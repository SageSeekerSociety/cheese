"""The sign-ins an account holds: list them, end one, end all but this one.

First slice of `app/api/routes/users.py` (arch review C-backend.md §3.3, the
"current user" cluster): the three `/users/me/sessions*` endpoints, which are
one concept — "where am I signed in, and get that browser out". They read and
write through `SessionService` and `TrustedDeviceService` only, share no
private helper with the rest of the account routes except the row-to-JSON
shape below, and already have their own integration file
(`tests/integration/test_sign_in_sessions.py`), so the block moves as a whole
and `users.py` keeps every other route untouched.

`_session_dto` needs the fields of a `UserSession` row and nothing else, so it
declares them structurally rather than importing the ORM model into a route
module — which C2 (`routes-touch-no-models` in `.importlinter`) exists to
prevent, and which would have made this move freeze a new exception. The
members are `Mapped[...]` because that is how the model declares them: pyright
compares the declared class attribute, so a plain ``id: uuid.UUID`` there does
not accept a ``Mapped[uuid.UUID]`` row. Reading one still yields the plain type.
"""

import uuid
from datetime import datetime
from typing import Annotated, Protocol

from fastapi import APIRouter, Depends, Path
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped

from app.auth.checker import require_auth_user
from app.auth.core import AuthUserInfo
from app.common.auth import get_current_session_id
from app.core.errors import NotFoundError
from app.db.session import get_db
from app.domain.user.sessions import RevokeReason, SessionService
from app.domain.user.trusted_devices import TrustedDeviceService

router = APIRouter(prefix="/users", tags=["Users"])


class _SessionRow(Protocol):
    """What `_session_dto` reads off a row returned by `SessionService.live`."""

    id: Mapped[uuid.UUID]
    login_method: Mapped[str]
    ip: Mapped[str]
    user_agent: Mapped[str]
    created_at: Mapped[datetime]
    last_used_at: Mapped[datetime]


def _session_dto(
    row: _SessionRow, current: uuid.UUID | None, trusted: set[uuid.UUID]
) -> dict:
    return {
        "id": str(row.id),
        "loginMethod": row.login_method,
        "ipAddress": row.ip,
        "userAgent": row.user_agent,
        "createdAt": row.created_at.isoformat(),
        "lastActiveAt": row.last_used_at.isoformat(),
        "current": row.id == current,
        # Its browser is trusted to skip two-step verification.
        "trusted": row.id in trusted,
    }


@router.get(
    "/me/sessions",
    summary="List active sessions",
)
async def list_sessions(
    auth_user: AuthUserInfo = Depends(require_auth_user),
    current: uuid.UUID | None = Depends(get_current_session_id),
    session: AsyncSession = Depends(get_db),
) -> dict:
    rows = await SessionService(session).live(auth_user.user_id)
    trusted = await TrustedDeviceService(session).trusted_sessions(auth_user.user_id)
    return {
        "code": 200,
        "message": "OK",
        "data": {"sessions": [_session_dto(row, current, trusted) for row in rows]},
    }


@router.delete(
    "/me/sessions/{sessionId}",
    summary="Revoke a session",
)
async def revoke_session(
    session_id: Annotated[uuid.UUID, Path(alias="sessionId")],
    auth_user: AuthUserInfo = Depends(require_auth_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """End one sign-in. Its refresh token stops working at once; an access
    token it already holds lasts out its few remaining minutes. Its browser
    is no longer trusted to skip two-step verification."""
    if not await SessionService(session).revoke(auth_user.user_id, session_id):
        raise NotFoundError("Session not found")
    await TrustedDeviceService(session).revoke_for_session(
        auth_user.user_id, session_id
    )
    return {
        "code": 200,
        "message": "Session revoked successfully.",
    }


@router.delete(
    "/me/sessions",
    summary="Revoke all other sessions",
)
async def revoke_all_sessions(
    auth_user: AuthUserInfo = Depends(require_auth_user),
    current: uuid.UUID | None = Depends(get_current_session_id),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """Sign out every device but the one asking, and stop trusting every
    browser but its own."""
    count = await SessionService(session).revoke_all(
        auth_user.user_id, RevokeReason.REVOKED, keep=current
    )
    await TrustedDeviceService(session).revoke_all(
        auth_user.user_id, keep_session=current
    )
    return {
        "code": 200,
        "message": f"Revoked {count} sessions.",
        "data": {
            "revokedCount": count,
        },
    }
