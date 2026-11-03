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

from app.auth.caller import handle_named_by
from app.auth.project_access import may_read_project
from app.common.auth import verify_access_token
from app.core import credentials
from app.core.config import GATEWAY_MOUNT
from app.domain.topic.services import TopicService

# Hop-by-hop headers a proxy must not forward (RFC 7230 §6.1) plus length/type,
# which the Response recomputes from the body it actually sends.
DROP_HEADERS = {
    "connection",
    "keep-alive",
    "transfer-encoding",
    "content-encoding",
    "content-length",
    "te",
    "trailer",
    "upgrade",
}


def browser_path(route_path: str) -> str:
    """The URL the browser used to reach this backend route."""
    return f"{GATEWAY_MOUNT}{route_path}"


def credential(conn: Request | WebSocket, cookie_name: str) -> str | None:
    """The caller's session token: ``Authorization: Bearer`` (normal API calls),
    ``?token=`` (the iframe/WebSocket the frontend builds), or the path-scoped
    cookie the page load dropped (the iframe's own sub-requests)."""
    return (
        credentials.presented(conn.headers)
        or conn.query_params.get("token")
        or conn.cookies.get(cookie_name)
        or None
    )


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

    2.0 membership is keyed by handle, and the token is turned into one by
    ``app.auth.caller.handle_named_by`` — the same answer the workspace routes
    read — rather than by reading the claims a second time here. Two readings
    of one token had already drifted apart: both took an all-digit NAME for a
    user id, and neither could find the ``User.handle`` column to answer with.
    """
    token = credential(conn, cookie_name)
    if not token:
        return False
    claims = verify_access_token(token)
    if claims is None:
        return False
    handle = await handle_named_by(session, claims)
    topic = await TopicService(session).get_or_404(topic_id)
    project_id = topic.project_id
    if project_id is None:
        return False
    return await may_read_project(session, project_id=project_id, handle=handle)
