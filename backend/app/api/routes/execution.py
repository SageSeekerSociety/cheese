"""Scoped tool calls to the room's recorded execution generation."""

import logging
import time
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
logger = logging.getLogger(__name__)


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
    trace_id = "execution-" + uuid.uuid4().hex
    logger.info(
        "execution_timing stage=handler_start trace=%s mono_ns=%d method=%s tool_id=%s",
        trace_id,
        time.monotonic_ns(),
        payload.method,
        payload.params.get("id", ""),
    )
    claims = scoped_token_claims(request.headers.get("x-cheese-token", ""))
    if not claims or claims.get("t") != str(topic_id):
        raise AuthenticationRequiredError("A credential for this room is required")
    place = await TopicService(db).place_or_404(topic_id)
    if claims.get("p") != str(place.project_id):
        raise ForbiddenError("Execution belongs to another project")
    placement = place.room.session_placement
    if (
        not placement
        or claims.get("r") != str(resource_id)
        or placement["resource_id"] != str(resource_id)
        or (place.room.resource_id or place.room.id) != resource_id
        or placement["execution"].get("kind") != "device"
    ):
        raise ConflictError("Execution generation is no longer current")
    if payload.method not in {
        "ping",
        "context",
        "context_fs",
        "invoke",
        "mcp",
        "control",
    }:
        raise ForbiddenError("This executor operation is not available to the session")
    target = placement["execution"]
    await db.commit()
    logger.info(
        "execution_timing stage=admitted trace=%s mono_ns=%d",
        trace_id,
        time.monotonic_ns(),
    )
    try:
        # Hold admission through the response, including background task creation.
        await execution.lock_release(db, resource_id, shared=True)
        return await execution.call(
            target, payload.method, payload.params, trace_id=trace_id
        )
    finally:
        await db.rollback()
        logger.info(
            "execution_timing stage=handler_end trace=%s mono_ns=%d",
            trace_id,
            time.monotonic_ns(),
        )
