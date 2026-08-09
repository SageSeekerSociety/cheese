"""Activity ingestion route — 线下接入 (spec §6.2, evals E1/E3).

"记一笔" / 导聊天记录 / 会议纪要 → 芝士 digests raw input into a structured
event topic via the activity-digestion skill + platform tools.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.deps import get_chat_service
from app.api.response import ok
from app.domain.agent.chat import ChatService

router = APIRouter(prefix="/api/projects", tags=["activities"])

ChatDep = Annotated[ChatService, Depends(get_chat_service)]


class ActivityIn(BaseModel):
    text: str = Field(min_length=1)
    author: str = "user-1"
    kind: str | None = None


@router.post("/{project_id}/activities")
async def ingest_activity(
    project_id: uuid.UUID, body: ActivityIn, chat: ChatDep
) -> dict:
    result = await chat.ingest_activity(
        project_id=project_id,
        text=body.text,
        author=body.author,
        kind_hint=body.kind,
    )
    return ok(result)


@router.post("/{project_id}/heartbeat")
async def run_heartbeat(project_id: uuid.UUID, chat: ChatDep) -> dict:
    """定期巡检 (eval G1): 芝士 inspects the project and sends graded alerts."""
    result = await chat.run_heartbeat(project_id=project_id)
    return ok(result)


@router.post("/{project_id}/summary")
async def summarize(project_id: uuid.UUID, chat: ChatDep) -> dict:
    """一页纸总结 (eval F2): 芝士 (re)writes the project's one-pager."""
    result = await chat.summarize_project(project_id=project_id)
    return ok(result)
