"""Exchange platform identity for read-only access to one published Site."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import ActorResolverDep
from app.api.response import ok
from app.core.db import get_db
from app.core.errors import AuthenticationRequiredError, NotFoundError
from app.domain.site.hosting import (
    AUTH_PATH,
    GRANT_TTL,
    content_origin,
    mint_site_token,
)
from app.domain.site.services import get_current_release, require_site_access

router = APIRouter(prefix="/projects", tags=["sites"])


@router.post("/{project_id}/site-session")
async def site_session(
    project_id: uuid.UUID,
    resolver: ActorResolverDep,
    db: Annotated[AsyncSession, Depends(get_db)],
    response: Response,
) -> dict:
    actor = await resolver.resolve(fallback_handle=None, project_id=project_id)
    if not actor.authenticated:
        raise AuthenticationRequiredError("请先登录")
    await require_site_access(db, actor.handle, project_id)
    if await get_current_release(db, project_id) is None:
        raise NotFoundError("暂无已发布的网站")
    response.headers["Cache-Control"] = "no-store"
    return ok(
        {
            "url": content_origin(project_id) + AUTH_PATH,
            "grant": mint_site_token(
                project_id, actor.handle, purpose="site-grant", ttl=GRANT_TTL
            ),
        }
    )
