"""The caller of a request a sandbox sent on its platform socket.

Only the process that owns the device links reaches the ``/machine`` routes
with the device connection secret (``app.api.machine_relay``); it names the
device and the state directory the request came in on, and the caller is
whichever session holds that state now (``domain.machine.caller``). Shared by
every route a sandbox may ask, in whichever route module serves it.
"""

import secrets
from typing import Annotated

from fastapi import Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.machine_relay import DEVICE_HEADER, SECRET_HEADER, STATE_HEADER
from app.core.config import settings
from app.core.db import get_db
from app.core.errors import AuthenticationRequiredError, ForbiddenError
from app.domain.machine.caller import MachineCaller, caller_of


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
        raise AuthenticationRequiredError()
    if not device or not state:
        raise AuthenticationRequiredError()
    caller = await caller_of(db, device, state)
    if caller is None:
        raise ForbiddenError()
    return caller


Caller = Annotated[MachineCaller, Depends(machine_caller)]
