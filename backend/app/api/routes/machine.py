"""What a session's sandbox may ask of the platform.

A sandbox holds no credential. Its request reaches these routes only through
the process that owns the device links (``app.api.machine_relay``), which names
the device and the state directory the request arrived on and vouches for both
with the device connection secret; the caller is whichever session holds that
state now (``domain.machine.caller``).
"""

import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.machine_relay import DEVICE_HEADER, SECRET_HEADER, STATE_HEADER
from app.core.config import settings
from app.core.db import get_db
from app.core.errors import AuthenticationRequiredError, ForbiddenError
from app.domain.machine.caller import MachineCaller, caller_of

router = APIRouter(prefix="/machine", tags=["machine"], include_in_schema=False)


async def machine_caller(
    db: Annotated[AsyncSession, Depends(get_db)],
    secret: Annotated[str | None, Header(alias=SECRET_HEADER)] = None,
    device: Annotated[str | None, Header(alias=DEVICE_HEADER)] = None,
    state: Annotated[str | None, Header(alias=STATE_HEADER)] = None,
) -> MachineCaller:
    expected = settings.device_connection_auth_secret
    # Bytes: a header Starlette decoded as Latin-1 may hold any character, and
    # comparing a non-ASCII str raises instead of refusing.
    given = (secret or "").encode("utf-8", "surrogateescape")
    if (
        not expected
        or not given
        or not secrets.compare_digest(given, expected.encode())
    ):
        raise AuthenticationRequiredError("machine requests come from the device link")
    if not device or not state:
        raise AuthenticationRequiredError("no device or state named")
    caller = await caller_of(db, device, state)
    if caller is None:
        raise ForbiddenError("no session holds this sandbox now")
    return caller


Caller = Annotated[MachineCaller, Depends(machine_caller)]


@router.get("/whoami")
async def whoami(caller: Caller) -> dict:
    """Whose this sandbox is, as the platform sees it from the socket alone."""
    return {
        "session": caller.session_id,
        "project": caller.project_id,
        "conversation": caller.conversation_id,
        "agent": caller.agent_handle,
    }
