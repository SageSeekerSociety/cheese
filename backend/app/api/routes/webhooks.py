"""External-facing webhook ingress — the front door for outside systems (CI,
deploy pipelines, ...) to post into a topic's timeline. Root-mounted (not
under /api): the caller is not a browser session, it's whatever holds the
token minted via POST /api/topics/{topic_id}/webhook-token.

Auth is the topic's own long-lived, revocable/rotatable webhook token (see
app.core.webhook_auth) — distinct from the cheese-CLI scoped token gate in
app.main, which this path is deliberately NOT listed under.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.response import ok
from app.core.db import async_session_factory, get_db
from app.core.errors import AuthenticationRequiredError, ValidationError
from app.domain.topic.repositories import TopicRepository
from app.domain.webhook import service as webhook_service

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

DbSession = Annotated[AsyncSession, Depends(get_db)]


def _caller_token(request: Request) -> str:
    """The webhook token, however the caller presents it: a dedicated header,
    or a bearer Authorization header (the common webhook-secret convention)."""
    direct = request.headers.get("x-webhook-token", "").strip()
    if direct:
        return direct
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return ""


@router.post("/{topic_id}")
async def receive_webhook(
    topic_id: uuid.UUID, body: dict, request: Request, db: DbSession
) -> dict:
    token = _caller_token(request)
    valid = bool(token) and await webhook_service.verify(
        db, topic_id=topic_id, token=token
    )
    if not valid:
        raise AuthenticationRequiredError("Invalid or revoked webhook token")

    content = (body.get("content") or "").strip()
    source = (body.get("source") or "").strip()
    if not content:
        raise ValidationError("content 不能为空")
    if not source:
        raise ValidationError("source 不能为空")

    topic = await TopicRepository(db).get(topic_id)
    if topic is None:
        raise AuthenticationRequiredError("Invalid or revoked webhook token")

    landed = await webhook_service.post_with_retries(
        async_session_factory,
        topic_id=topic_id,
        content=content,
        source=source,
    )
    return ok({"accepted": landed})
