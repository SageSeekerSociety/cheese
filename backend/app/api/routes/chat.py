"""Chat WebSocket route.

Protocol (matches the frontend contract):
  client → {"type":"message","content": str, "author": str}
  server → {"type":"user_block","block": Block}
           {"type":"delta","text": str}          (token streaming)
           {"type":"assistant_block","block": Block}
           {"type":"error","message": str}
           {"type":"done"}
"""

import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect

from app.api.deps import get_chat_service
from app.core.errors import AppError
from app.domain.agent.chat import ChatService

router = APIRouter(tags=["chat"])
logger = logging.getLogger("cheesex.chat")


async def _safe_send(websocket: WebSocket, frame: dict) -> None:
    """Send a frame, tolerating an already-closed socket (client navigated away
    mid-turn) so error handling never raises a second exception."""
    try:
        await websocket.send_json(frame)
    except (RuntimeError, WebSocketDisconnect):
        pass


@router.websocket("/api/topics/{topic_id}/chat")
async def chat(
    websocket: WebSocket,
    topic_id: uuid.UUID,
    chat_service: Annotated[ChatService, Depends(get_chat_service)],
) -> None:
    await websocket.accept()
    try:
        while True:
            payload = await websocket.receive_json()
            if payload.get("type") != "message":
                await websocket.send_json(
                    {"type": "error", "message": "unsupported message type"}
                )
                continue

            content = (payload.get("content") or "").strip()
            author = payload.get("author") or "anonymous"
            # @芝士 toggle: summon the AI, or just post (spec C3, default post).
            summon = bool(payload.get("summon", False))
            if not content:
                await websocket.send_json({"type": "error", "message": "empty content"})
                continue

            try:
                # A turn's work (doc edits, decisions, the AI's reply) is real and
                # persisted regardless of who is watching, so a client disconnect
                # must NOT cancel it. We keep draining converse to completion (so
                # it commits) and merely stop pushing frames to a dead socket —
                # sending to a closed socket is the only thing that would crash.
                # The user sees the result (persisted blocks) on reconnect.
                live = True
                async for frame in chat_service.converse(
                    topic_id=topic_id,
                    author=author,
                    content=content,
                    summon=summon,
                ):
                    if not live:
                        continue
                    try:
                        await websocket.send_json(frame)
                    except (WebSocketDisconnect, RuntimeError):
                        logger.info(
                            "client disconnected mid-turn for topic %s; "
                            "finishing the turn in the background",
                            topic_id,
                        )
                        live = False
            except AppError as exc:
                await _safe_send(websocket, {"type": "error", "message": exc.message})
            except Exception:  # surface agent/runtime failures (spec H4)
                # Log the real cause (it's otherwise lost) and tell the user
                # plainly — a turn may die on a transient sandbox/model error, but
                # whatever 芝士 already committed (e.g. the doc) is saved.
                logger.exception("chat turn failed for topic %s", topic_id)
                await _safe_send(
                    websocket,
                    {
                        "type": "error",
                        "message": "芝士这轮中断了（偶发的沙箱/模型错误）。"
                        "它已完成的改动已保存，再 @ 它一次就会接着来。",
                    },
                )
    except WebSocketDisconnect:
        return
