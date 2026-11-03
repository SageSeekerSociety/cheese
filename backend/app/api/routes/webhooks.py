"""External-facing webhook ingress — the front door for outside systems (CI,
deploy pipelines, ...) to post into a topic's timeline. Root-mounted (not
under /api): the caller is not a browser session, it's whatever holds the
token minted via POST /api/topics/{topic_id}/webhook-token.

Auth is the topic's own long-lived, revocable/rotatable webhook token (see
app.core.webhook_auth) — distinct from the cheese-CLI scoped token gate in
app.main, which this path is deliberately NOT listed under.
"""

import uuid

from fastapi import APIRouter, Request

from app.api.request_deps import DbSession
from app.api.response import ok
from app.api.write_access import ROUTE_DECIDES
from app.core import credentials
from app.core.db import async_session_factory
from app.core.errors import AuthenticationRequiredError, ValidationError
from app.core.sentences import say
from app.domain.agent.announce import announce
from app.domain.project.services import refuse_writes_if_archived
from app.domain.topic.repositories import TopicRepository
from app.domain.webhook import service as webhook_service

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/{topic_id}", dependencies=[ROUTE_DECIDES])
async def receive_webhook(
    topic_id: uuid.UUID, body: dict, request: Request, db: DbSession
) -> dict:
    token = credentials.presented(request.headers)
    valid = bool(token) and await webhook_service.verify(
        db, topic_id=topic_id, token=token
    )
    if not valid:
        raise AuthenticationRequiredError("Invalid or revoked webhook token")

    content = (body.get("content") or "").strip()
    source = (body.get("source") or "").strip()
    if not content:
        raise ValidationError(say("contentRequired"))
    if not source:
        raise ValidationError(say("sourceRequired"))

    topic = await TopicRepository(db).get(topic_id)
    if topic is None:
        raise AuthenticationRequiredError("Invalid or revoked webhook token")
    # Authenticated by the webhook's own token rather than ActorResolver, so the
    # archived-project refusal every other write gets is asked here.
    await refuse_writes_if_archived(db, topic.project_id)

    landed = await webhook_service.post_with_retries(
        async_session_factory,
        poster=announce,
        topic_id=topic_id,
        content=content,
        source=source,
    )
    return ok({"accepted": landed})
