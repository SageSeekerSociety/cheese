"""The platform signs a short-lived grant; only the content host sets its cookie."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import ActorResolverDep
from app.api.preview_host import (
    AUTH_PATH,
    GRANT_TTL,
    mint_preview_token,
    preview_origin,
    require_preview_access,
)
from app.api.response import ok
from app.core.db import get_db
from app.core.errors import AuthenticationRequiredError

router = APIRouter(prefix="/topics", tags=["preview"])


@router.post("/{topic_id}/preview-session")
async def preview_session(
    topic_id: uuid.UUID,
    resolver: ActorResolverDep,
    db: Annotated[AsyncSession, Depends(get_db)],
    response: Response,
) -> dict:
    actor = await resolver.resolve(fallback_handle=None, topic_id=topic_id)
    if not actor.authenticated:
        raise AuthenticationRequiredError("请先登录")
    await require_preview_access(db, topic_id, actor.handle)
    response.headers["Cache-Control"] = "no-store"
    return ok(
        {
            "url": preview_origin(topic_id) + AUTH_PATH,
            "grant": mint_preview_token(
                topic_id, actor.handle, purpose="preview-grant", ttl=GRANT_TTL
            ),
        }
    )
