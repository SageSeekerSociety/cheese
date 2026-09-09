"""Scoped tool calls to the room's recorded execution generation."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.errors import AuthenticationRequiredError, ConflictError, ForbiddenError
from app.core.sandbox_auth import scoped_token_claims
from app.domain.agent import execution
from app.domain.topic.services import TopicService

router = APIRouter(tags=["execution"])


class ExecutionRequest(BaseModel):
    method: str
    params: dict = {}


@router.post("/topics/{topic_id}/execution/{resource_id}", include_in_schema=False)
async def execute(
    topic_id: uuid.UUID,
    resource_id: uuid.UUID,
    request: Request,
    payload: ExecutionRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    claims = scoped_token_claims(request.headers.get("x-cheese-token", ""))
    if not claims or claims.get("t") != str(topic_id):
        raise AuthenticationRequiredError("A credential for this room is required")
    place = await TopicService(db).place_or_404(topic_id)
    if claims.get("p") != str(place.project_id):
        raise ForbiddenError("Execution belongs to another project")
    placement = place.room.session_placement
    if (
        not placement
        or placement["resource_id"] != str(resource_id)
        or (place.room.resource_id or place.room.id) != resource_id
        or placement["execution"].get("kind") != "device"
    ):
        raise ConflictError("Execution generation is no longer current")
    if payload.method not in {"ping", "context", "invoke", "mcp", "control"}:
        raise ForbiddenError("This executor operation is not available to the session")
    target = placement["execution"]
    await db.commit()
    return await execution.call(target, payload.method, payload.params)
