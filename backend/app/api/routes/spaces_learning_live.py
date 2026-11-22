"""「这块板的学情分析变了」的一条只读推送。

两页（「我的学习洞察」「数据 › 学情分析」）不必再靠人刷新：

    WS /spaces/{spaceId}/learning-analysis/live?token=<会话令牌>

帧只有一种 ``{"type": "space_learning", "spaceId": n}``：这块板的分析结果变了，自己
重读那份 GET。**帧里不带任何分析结果** —— 谁能看到哪一条判断仍由那两条 GET 判（只有
本人、不足 3 人不分析），推送只是一声「重新读」，走漏一帧最多让页面晚一轮。

门与读那一页一样：``view``（能看见这块板就能订阅）。令牌在查询串里，因为浏览器给
WebSocket 设不了请求头（与 ``/notifications/inbox`` 同一个办法）；判据仍是那一份
``ActorResolver``。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect

from app.api.auth import ActorResolver
from app.api.deps import get_broker, get_chat_service
from app.api.request_deps import DbSession
from app.domain.agent.chat import ChatService
from app.domain.agent.realtime.broker import InProcessBroker
from app.domain.space import learning_live
from app.domain.space import permissions as space_seats
from app.domain.space.permissions import Permission as SpacePermission

router = APIRouter(prefix="/spaces", tags=["Spaces"])
logger = logging.getLogger(__name__)

#: Close code: the credential does not hold.
SIGNED_OUT = 1008
#: A beat so a dead connection is noticed and a signed-out one closed.
BEAT_SECONDS = 25.0


@router.websocket("/{space_id}/learning-analysis/live")
async def learning_live_feed(
    websocket: WebSocket,
    space_id: int,
    chat: Annotated[ChatService, Depends(get_chat_service)],
    broker: Annotated[InProcessBroker, Depends(get_broker)],
    db: DbSession,
) -> None:
    token = websocket.query_params.get("token") or ""
    actor = await ActorResolver(
        session=db, bearer=token or None, cheese_token=""
    ).resolve(read_only=True)
    from app.domain.user.services import user_by_handle

    owner = (
        await user_by_handle(db, actor.handle)
        if actor.authenticated and actor.user_id is None
        else None
    )
    user_id = actor.user_id or (owner.id if owner is not None else None)
    allowed = (
        actor.authenticated
        and user_id is not None
        and await space_seats.SPACE.allows(db, space_id, user_id, SpacePermission.view)
    )
    if not allowed:
        await websocket.accept()
        await websocket.close(code=SIGNED_OUT)
        return
    await websocket.accept()
    with contextlib.suppress(WebSocketDisconnect):
        async with broker.subscribe(learning_live.channel(space_id)) as frames:
            # 第一帧：现在是这样，页面收下就当「刚读过」。
            await websocket.send_json(learning_live.frame(space_id))
            beat = asyncio.create_task(asyncio.sleep(BEAT_SECONDS))
            hung_up = asyncio.create_task(websocket.receive())
            try:
                while True:
                    getter = asyncio.create_task(frames.get())
                    done, _ = await asyncio.wait(
                        {getter, hung_up, beat}, return_when=asyncio.FIRST_COMPLETED
                    )
                    if hung_up in done:
                        getter.cancel()
                        return
                    if getter in done:
                        getter.result()
                        await websocket.send_json(learning_live.frame(space_id))
                    else:
                        getter.cancel()
                    if beat in done:
                        beat = asyncio.create_task(asyncio.sleep(BEAT_SECONDS))
                        await websocket.send_json({"type": "beat"})
            finally:
                beat.cancel()
                hung_up.cancel()
