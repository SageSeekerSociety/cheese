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
from app.core.errors import AuthenticationRequiredError
from app.core.sentences import say
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

    A bearer that WAS presented and did not verify raises 401 rather than
    answering ``False``. The route reads ``False`` as 「这个项目不存在」 and
    answers 404 with no ``error.i18n``, so a browser holding a token the server
    can no longer check — expired, or signed for another deployment — was told
    the project was gone, by a sentence it could not act on: the frontend
    refreshes on 401 and retries, and has nothing to do with a 404. It is
    ``app.api.auth.reject_failed_credential``'s rule, for a route that has no
    ``Actor`` to hand it: 认不出 is an answer, but only no-credential-at-all
    may be treated as anonymous. With no ``Authorization`` header and no
    ``token`` parameter nothing was presented, so genuinely anonymous traffic
    still gets ``False``.
    """
    credential = _bearer(request)
    handle = await caller_handle(request, session)
    if handle is None and credential is not None:
        raise AuthenticationRequiredError(say("sessionExpired"))
    return await may_read_project(session, project_id=project_id, handle=handle)
