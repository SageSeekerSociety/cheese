"""Who is calling, expressed as a handle.

2.0 membership is keyed by handle, so these routes ask for the handle an access
token names rather than its user id. A token that names no handle is resolved
through its user id.
"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app.auth.project_access import may_read_project
from app.common.auth import verify_access_token
from app.domain.user.repositories import UserRepository


def _bearer(request: Request) -> str | None:
    header = request.headers.get("authorization") or ""
    if header.lower().startswith("bearer "):
        return header[7:].strip() or None
    # A browser cannot set a header on an iframe or a WebSocket, so those carry
    # the same token in the query string instead.
    return request.query_params.get("token")


async def caller_handle(request: Request, session: AsyncSession) -> str | None:
    """The calling user's handle, or None when nobody is authenticated."""
    token = _bearer(request)
    claims = verify_access_token(token) if token else None
    if claims is None:
        return None
    # A handle that is all digits is a user id standing in for a missing one.
    if not claims.handle.isdigit():
        return claims.handle
    user = await UserRepository(session).get_by_id(int(claims.handle))
    return getattr(user, "handle", None)


async def may_access_project(
    request: Request, session: AsyncSession, project_id: uuid.UUID
) -> bool:
    """Whether the caller may read this project's conversations.

    The claim set itself lives in ``app.auth.project_access`` — one answer for
    every route that reads a project's conversations, rather than one per route.
    This function is only the HTTP-shaped half: pull the credential off the
    request, then ask.
    """
    return await may_read_project(
        session, project_id=project_id, handle=await caller_handle(request, session)
    )
