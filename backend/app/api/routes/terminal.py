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
from app.domain.agent.harness import HARNESSES, harness_name
from app.domain.agent_session.services import AgentSessionService
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


async def _screen_is_watchable(db: AsyncSession, room_id: uuid.UUID) -> bool:
    """Does this room's harness put anything in the pane it was started in?

    The session sitting in this room is the only place this is known — the
    screen itself carries no harness. There is one pane per room and it belongs
    to whichever session last opened one, so that session is asked and no other:
    a room whose claude-code teammate has been replaced by pi still carries the
    older row, and reading it would promise a pane that stays black. A room that
    has never run has no placed session and gets the default, which is the
    harness a room without an opinion runs.
    """
    name = await AgentSessionService(db).harness_in_room(room_id)
    harness = HARNESSES.get(harness_name(name))
    # 名字不在注册表里，答的就是 False。那是一条旧会话行，写着一个这套部署已经不
    # 跑的骨架（结论 43 摘掉过两个），它的屏里有什么谁也说不上来——而答 True 的代
    # 价是把一块黑屏摆到人脸前，还顺手收走 施工记录。没跑过的房间不走这条：它没
    # 有会话行，``harness_name(None)`` 给的是这套部署跑的那个。
    return harness is not None and harness.draws_on_its_screen


@router.get("/{topic_id}/terminal")
async def terminal_status(topic_id: uuid.UUID, request: Request, db: DbSession) -> dict:
    """Whether this topic has a live pane to watch, and where to attach.

    ``available`` must answer "will the drawer actually show a pane?", because
    that is the only question the frontend asks it — a true here means the drawer
    replaces the 施工记录 timeline with the embed. So a caller without a
    credential is told False rather than being handed an address that would then
    refuse them, which is what left users staring at a blank frame with no way
    back to the timeline.

    An open screen is not that answer either, and for the same reason. Claude
    Code IS the screen's program, so watching its pane is watching the work; a
    harness that runs a RUNNER there and drives the agent over RPC leaves a pane
    that stays empty for the life of the session. Answering True for one of
    those puts the same black frame in front of the same person — the
    harness registry is asked instead (``draws_on_its_screen``), so adding a
    harness settles this where every other thing about it is settled.

    """
    place = await TopicService(db).place_or_404(topic_id)
    if not await proxy.may_view_topic(db, place.room_id, request, COOKIE_NAME):
        return ok({"available": False})
    if not await _screen_is_watchable(db, place.room_id):
        return ok({"available": False})
    sid = _device_screen_id(place.room_id)
    if sid is None:
        return ok({"available": False})
    return ok(
        {
            "available": True,
            # Typing only reaches an agent that is reading the pane. The same
            # registry answers both, because it is the same fact.
            "interactive": True,
            "ws": f"/connector/session/{sid}/screen",
        }
    )
