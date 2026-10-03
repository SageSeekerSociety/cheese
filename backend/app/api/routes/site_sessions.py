"""Exchange platform identity for read-only access to one published Site."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import ActorResolverDep
from app.api.response import ok
from app.core.db import get_db
from app.core.errors import AuthenticationRequiredError, NotFoundError
from app.domain.block.notice_text import say
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
    # Viewing a published site stays open after the project is archived: the
    # site is kept like the rest of its data, and this POST only mints a grant.
    actor = await resolver.resolve(project_id=project_id, read_only=True)
    if not actor.authenticated:
        raise AuthenticationRequiredError(say("signInFirst"))
    await require_site_access(db, actor.handle, project_id)
    if await get_current_release(db, project_id) is None:
        raise NotFoundError(say("noPublishedSite"))
    response.headers["Cache-Control"] = "no-store"
    return ok(
        {
            "url": content_origin(project_id) + AUTH_PATH,
            "grant": mint_site_token(
                project_id, actor.handle, purpose="site-grant", ttl=GRANT_TTL
            ),
        }
    )
