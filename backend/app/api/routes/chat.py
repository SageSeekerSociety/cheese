"""Chat WebSocket route.

Protocol (matches the frontend contract):
  client → {"type":"message","content": str, "author": str}
  server → {"type":"user_block","block": Block}
           {"type":"delta","text": str}          (token streaming)
           {"type":"assistant_block","block": Block}
           {"type":"error","message": str}
           {"type":"done"}
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect

from app.api.deps import get_chat_service
from app.core.errors import AppError
from app.domain.agent.chat import ChatService

router = APIRouter(tags=["chat"])


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
                async for frame in chat_service.converse(
                    topic_id=topic_id,
                    author=author,
                    content=content,
                    summon=summon,
                ):
                    await websocket.send_json(frame)
            except AppError as exc:
                await websocket.send_json({"type": "error", "message": exc.message})
            except Exception as exc:  # surface agent/runtime failures (spec H4)
                await websocket.send_json(
                    {"type": "error", "message": f"agent error: {exc}"}
                )
    except WebSocketDisconnect:
        return
