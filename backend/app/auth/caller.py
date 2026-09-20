"""Who is calling, expressed as a handle.

Two token families reach these routes and only one of them survives the numeric
identity layer:

  * main-minted tokens carry an int user id in ``sub`` — ``get_auth_user`` turns
    those into an ``AuthUserInfo`` and they work;
  * cheesex session tokens are HANDLE-ONLY (``user_id=None``), so the same layer
    sees no id and reports a guest.

2.0 membership is keyed by handle, so a check written against ``AuthUserInfo``
silently rejects every caller in the second family — which is why guarding these
routes by user id refused real callers. Resolving the handle from the claims,
with the numeric id as a fallback, is the only form that covers both.
"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app.auth.project_access import may_read_project
from app.core.tokens import verify_session_token
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
    if token:
        claims = verify_session_token(token)
        if claims is not None:
            handle = claims.get("handle") or claims.get("sub")
            # A numeric ``sub`` is a user id, not a handle — resolve it.
            if handle and not str(handle).isdigit():
                return str(handle)
            if handle:
                user = await UserRepository(session).get_by_id(int(handle))
                return getattr(user, "handle", None)
    return None


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
