"""Scoped tool calls to the room's recorded execution generation."""

import logging
import time
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.errors import (
    AuthenticationRequiredError,
    ConflictError,
    ForbiddenError,
    GatewayTimeoutError,
    NotFoundError,
)
from app.core.sandbox_auth import scoped_token_claims
from app.domain.agent import execution
from app.domain.device import owner_reads
from app.domain.topic.models import Topic

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
    logger.debug(
        "execution_timing stage=handler_start trace=%s mono_ns=%d method=%s tool_id=%s",
        trace_id,
        time.monotonic_ns(),
        payload.method,
        payload.params.get("id", ""),
    )
    claims = scoped_token_claims(request.headers.get("x-cheese-token", ""))
    if not claims or claims.get("t") != str(topic_id):
        raise AuthenticationRequiredError("A credential for this room is required")
    # The connection owner survives app releases. Loading the full Topic model
    # makes an unrelated column removal break every tool call on the old owner.
    room = (
        await db.execute(
            select(Topic.id, Topic.project_id, Topic.resource_id).where(
                Topic.id == topic_id
            )
        )
    ).one_or_none()
    if room is None:
        raise NotFoundError("Topic not found")
    if claims.get("p") != str(room.project_id):
        raise ForbiddenError("Execution belongs to another project")
    # The hands are the session's, so the lease is read per session — one room
    # can hold several. The credential names a room and a generation and not a
    # session, which is enough because the executor is still pinned per room
    # (`resolve_pinned_device`): every session here leases the same hands. The
    # day that stops being true, the credential has to say which session it
    # belongs to.
    lease = next(
        (
            hands
            for where, hands in await owner_reads.session_places(db, topic_id)
            if where.get("resource_id") == str(resource_id)
            and (hands or {}).get("kind") == "device"
        ),
        None,
    )
    if (
        lease is None
        or claims.get("r") != str(resource_id)
        or (room.resource_id or room.id) != resource_id
    ):
        raise ConflictError("Execution generation is no longer current")
    if payload.method not in {
        "ping",
        "context",
        "context_fs",
        "invoke",
        "mcp",
        "control",
        "cli",
    }:
        raise ForbiddenError("This executor operation is not available to the session")
    target = lease
    await db.commit()
    logger.debug(
        "execution_timing stage=admitted trace=%s mono_ns=%d",
        trace_id,
        time.monotonic_ns(),
    )
    # The scope check above is the only database work this request needs.  Do
    # not acquire a transaction-scoped advisory lock here: the remote executor
    # call can legitimately stay open for minutes, and holding the request's
    # AsyncSession across it consumes one QueuePool slot per active tool.  Once
    # enough tools are in flight, ordinary page reads wait for
    # db_pool_timeout_s and the whole site appears dead.  The device connection
    # owner already counts active RPCs and gates executor release, so the
    # business route must release its database connection before crossing that
    # boundary.
    try:
        return await execution.call(
            target, payload.method, payload.params, trace_id=trace_id
        )
    except TimeoutError as exc:
        # The machine holds its link and does not answer. That is a fault of the
        # far end, and answering 500「服务器内部错误」 blames the one process it
        # cannot be — the same reasoning, and the same status, as the connection
        # owner's own RPC path in `device_connection_app.call`.
        raise GatewayTimeoutError("机器没有在时限内回应这次执行调用") from exc
    finally:
        await db.rollback()
        logger.debug(
            "execution_timing stage=handler_end trace=%s mono_ns=%d",
            trace_id,
            time.monotonic_ns(),
        )
