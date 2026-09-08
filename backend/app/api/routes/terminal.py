"""施工现场 real terminal (spec §7.1): where to watch a topic's live pane.

A turn runs on someone's enrolled machine, and that machine is behind NAT — so
its pane travels back over the link the connector already dialled out on
(``/connector/session/{sid}/screen``). This endpoint's whole job is to tell the
frontend whether such a screen is open for the topic and which one to attach to.

Authorization: a member/owner of the topic's project (``proxy.may_view_topic``).
The credential rides as ``?token=`` because a browser can set no header on a
WebSocket; the same check runs again when the screen socket is opened, so this
answer is a hint to the UI, never the access decision.

A place is a room OR a thread in it, and both run panes: a thread's screen is
opened under the thread's own id (``device_provider`` passes the place id as the
screen's ``topic_id``), so the lookup below finds it as soon as the id is allowed
to name one. The roster it is checked against is still the room's, because that
is the only roster there is.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import proxy
from app.api.response import ok
from app.core.db import get_db
from app.domain.agent.device_hub import device_hub
from app.domain.topic.services import TopicService

router = APIRouter(prefix="/topics", tags=["terminal"])

DbSession = Annotated[AsyncSession, Depends(get_db)]

# Sub-request credential for the iframe's own fetches. Scoped to this topic's
# ``…/terminal`` path — see app.api.proxy.
COOKIE_NAME = "cheesex_proxy"


def _device_screen_id(topic_id: uuid.UUID) -> str | None:
    """The live screen a device is running this topic in, if any."""
    for screen in device_hub.all_online_screens():
        if screen.topic_id == topic_id:
            return screen.sid
    return None


@router.get("/{topic_id}/terminal")
async def terminal_status(topic_id: uuid.UUID, request: Request, db: DbSession) -> dict:
    """Whether this topic has a live pane to watch, and where to attach.

    ``available`` must answer "will the drawer actually show a pane?", because
    that is the only question the frontend asks it — a true here means the drawer
    replaces the 施工记录 timeline with the embed. So a caller without a
    credential is told False rather than being handed an address that would then
    refuse them, which is what left users staring at a blank frame with no way
    back to the timeline.

    """
    place = await TopicService(db).place_or_404(topic_id)
    if not await proxy.may_view_topic(db, place.room_id, request, COOKIE_NAME):
        return ok({"available": False})
    sid = _device_screen_id(place.room_id)
    if sid is None:
        return ok({"available": False})
    return ok(
        {
            "available": True,
            "interactive": True,
            "ws": f"/connector/session/{sid}/screen",
        }
    )
