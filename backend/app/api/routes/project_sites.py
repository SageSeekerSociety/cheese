"""Project delivery: publish the accepted static website as a private Site."""

import asyncio
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import ActorResolverDep
from app.api.response import ok
from app.core.db import get_db
from app.core.errors import AppError, AuthenticationRequiredError, BaseError
from app.domain.site.hosting import content_origin
from app.domain.site.services import (
    BUILD_REQUIRED,
    can_publish_site,
    get_current_release,
    publication_source,
    publish_site,
    require_site_access,
    site_metadata,
)

router = APIRouter(prefix="/projects", tags=["sites"])
DbSession = Annotated[AsyncSession, Depends(get_db)]


class PublishSiteBody(BaseModel):
    directory: str = Field(min_length=1, max_length=1024)
    expected_source_revision: str = Field(pattern=r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")


@router.get("/{project_id}/site")
async def get_project_site(
    project_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    actor = await resolver.resolve(fallback_handle=None, project_id=project_id)
    if not actor.authenticated:
        raise AuthenticationRequiredError("请先登录")
    await require_site_access(db, actor.handle, project_id)
    source = await asyncio.to_thread(publication_source, project_id)
    release = await get_current_release(db, project_id)
    can_publish = await can_publish_site(db, actor.handle, project_id)
    reason = None
    try:
        content_origin(project_id)
    except (AppError, BaseError) as exc:
        reason = str(exc)
    if reason is None and not source["candidates"]:
        reason = BUILD_REQUIRED
    return ok(
        {
            **source,
            "can_publish": can_publish,
            "site": site_metadata(release) if release is not None else None,
            "unavailable_reason": reason,
        }
    )


@router.post("/{project_id}/site")
async def publish_project_site(
    project_id: uuid.UUID,
    body: PublishSiteBody,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    actor = await resolver.resolve(fallback_handle=None, project_id=project_id)
    if not actor.authenticated:
        raise AuthenticationRequiredError("请先登录")
    await require_site_access(db, actor.handle, project_id)
    content_origin(project_id)
    release = await publish_site(
        db,
        project_id,
        handle=actor.handle,
        directory=body.directory,
        expected_source_revision=body.expected_source_revision,
    )
    # Request-scoped dependencies finish after the response is sent. Publication
    # must commit here so a failed commit cannot already have reported success.
    await db.commit()
    return ok(site_metadata(release))
