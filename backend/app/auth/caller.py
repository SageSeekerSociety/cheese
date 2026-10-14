"""Who is calling, expressed as a handle.

2.0 membership is keyed by handle, so these routes ask for the handle an access
token names rather than its user id. A token that names no handle is resolved
through its user id.

A handle is the person's NAME — the string every authorization key in the
platform is spelled with (``member_handle``, ``owner_handle``) — and
:func:`handle_named_by` is the single answer to "which name does this token
carry", shared with the live-view gate in ``app.api.proxy`` so that the two
cannot come to different conclusions about the same token again.
"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app.auth.project_access import may_read_project
from app.common.auth import AccessClaims, verify_access_token
from app.core.errors import AuthenticationRequiredError
from app.core.obs import get_logger
from app.core.sentences import say
from app.domain.user.repositories import UserRepository

_log = get_logger("cheesex.auth")


def _bearer(request: Request) -> str | None:
    header = request.headers.get("authorization") or ""
    if header.lower().startswith("bearer "):
        return header[7:].strip() or None
    # A browser cannot set a header on an iframe or a WebSocket, so those carry
    # the same token in the query string instead.
    return request.query_params.get("token")


async def handle_named_by(session: AsyncSession, claims: AccessClaims) -> str:
    """The handle the caller of ``claims`` goes by.

    A token that states its ``handle`` claim names the person directly, and
    that value IS the handle. An all-digit one is no less a name: usernames are
    ``[a-zA-Z0-9_-]{4,32}`` (``app.domain.user.services``), so "2026201782" is
    an ordinary account name, and ``isdigit`` says nothing about whether a
    value is a name or an id.

    Reading one as the other is what this replaces: a caller named
    ``2026201782`` was taken for user id 2026201782, no such user existed, and
    the caller was told their sign-in had lapsed — on the files and git-diff
    routes alone, because every other route resolves the caller through
    ``ActorResolver``, which repairs the same confusion
    (``app.api.auth._recover_numeric_handle``).

    Only a token that states no claim carries an id: its ``handle`` is then
    ``sub``, the int user PK, and the name has to come from the row. An id that
    resolves to nobody keeps its value — authorization still denies, as it must
    — and is logged rather than denied in silence.
    """
    if claims.handle_from_claim:
        return claims.handle
    if claims.user_id is not None:
        user = await UserRepository(session).get_by_id(claims.user_id)
        if user is not None:
            return user.username
        _log.warning("token_handle_unresolved", user_id=claims.user_id)
    return claims.handle


async def caller_handle(request: Request, session: AsyncSession) -> str | None:
    """The calling user's handle, or None when nobody is authenticated."""
    token = _bearer(request)
    claims = verify_access_token(token) if token else None
    if claims is None:
        return None
    return await handle_named_by(session, claims)


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
