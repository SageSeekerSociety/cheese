"""Who may look inside a topic's live workings, and how they prove it.

A browser cannot set an ``Authorization`` header on an ``<iframe>`` or a
``WebSocket``, so the session token rides as ``?token=`` (or as a path-scoped
cookie a page load left behind). Reading it back and turning it into "is this
person on this project" is one question with one answer, so it lives here rather
than in each surface that asks it.
"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request
from starlette.websockets import WebSocket

from app.core.tokens import verify_session_token
from app.domain.membership.repositories import MemberRepository
from app.domain.project.repositories import ProjectRepository
from app.domain.topic.services import TopicService
from app.domain.user.repositories import UserRepository

# How long the path-scoped sub-request cookie stays valid. Short: it only has to
# outlive one open drawer, and it is re-issued on every page load.
COOKIE_TTL_S = 8 * 3600


def credential(conn: Request | WebSocket, cookie_name: str) -> str | None:
    """The caller's session token: ``Authorization: Bearer`` (normal API calls),
    ``?token=`` (the iframe/WebSocket the frontend builds), or the path-scoped
    cookie the page load dropped (the iframe's own sub-requests)."""
    header = conn.headers.get("authorization") or ""
    if header.lower().startswith("bearer "):
        token = header[7:].strip()
        if token:
            return token
    return conn.query_params.get("token") or conn.cookies.get(cookie_name) or None


async def _resolve_handle(session: AsyncSession, token: str) -> str | None:
    """Handle behind a session token, or None. main-minted tokens put the int user
    id in ``sub`` and the username in ``handle``; legacy cheesex ones are
    handle-only — 2.0 membership is keyed by handle, so both must land on one."""
    claims = verify_session_token(token)
    if claims is None:
        return None
    handle = claims["handle"] or claims["sub"]
    if not handle:
        return None
    if str(handle).isdigit():
        user = await UserRepository(session).get_by_id(int(handle))
        return getattr(user, "handle", None)
    return str(handle)


async def may_view_topic(
    session: AsyncSession,
    topic_id: uuid.UUID,
    conn: Request | WebSocket,
    cookie_name: str,
) -> bool:
    """Whether this caller may look inside the topic's live workings.

    A topic id is a UUID, but that is obscurity, not authorization: the pane
    shows whatever the agent is doing, so anyone who learns an id would otherwise
    get a read of the project's contents. Gate is the same as the device
    viewer's: a logged-in member or owner of the topic's project.
    """
    token = credential(conn, cookie_name)
    if not token:
        return False
    handle = await _resolve_handle(session, token)
    if not handle:
        return False
    topic = await TopicService(session).get_or_404(topic_id)
    project_id = topic.project_id
    if project_id is None:
        return False
    if await MemberRepository(session).get(project_id=project_id, user_handle=handle):
        return True
    project = await ProjectRepository(session).get(project_id)
    return project is not None and project.owner_handle == handle
