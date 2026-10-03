"""Sign either a live grant or a resource-bound, independently isolated grant."""

import asyncio
import mimetypes
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import ActorResolverDep
from app.api.preview_host import (
    APP_MIME,
    AUTH_PATH,
    GRANT_TTL,
    mint_preview_token,
    preview_origin,
    require_preview_access,
    resource_key,
)
from app.api.response import ok
from app.core.config import settings
from app.core.db import get_db
from app.core.errors import AuthenticationRequiredError, NotFoundError
from app.domain.agent.preview_hub import preview_hub
from app.domain.agent.preview_owner import inspect_owner
from app.domain.block.notice_text import say
from app.domain.block.queries import latest_preview_for_room
from app.domain.library import service as library
from app.domain.project.room_files import clean_artifact_path

router = APIRouter(prefix="/topics", tags=["preview"])


class PreviewSelection(BaseModel):
    artifact_id: uuid.UUID | None = None
    path: str | None = Field(default=None, max_length=4096)
    version: str | None = Field(default=None, max_length=64)
    instance: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


@router.post("/{topic_id}/preview-session")
async def preview_session(
    topic_id: uuid.UUID,
    resolver: ActorResolverDep,
    db: Annotated[AsyncSession, Depends(get_db)],
    response: Response,
    selection: PreviewSelection | None = None,
) -> dict:
    actor = await resolver.resolve(topic_id=topic_id)
    if not actor.authenticated:
        raise AuthenticationRequiredError(say("signInFirst"))
    place = await require_preview_access(db, topic_id, actor.handle)
    resource = None
    if selection is not None:
        artifact = None
        if selection.artifact_id:
            artifact = await latest_preview_for_room(db, place.room_id)
            if artifact is None or artifact.id != selection.artifact_id:
                raise NotFoundError("Preview selection changed")
        if artifact and artifact.mime_type == APP_MIME:
            if settings.preview_connection_mode == "owner":
                inspection = await inspect_owner(
                    topic_id, artifact.author, expected_instance=selection.instance
                )
                instance = inspection.instance if inspection.state == "online" else None
            else:
                instance = await preview_hub.instance(topic_id, artifact.author)
            if not instance or selection.instance != instance:
                raise NotFoundError("Preview instance gone or unavailable")
            resource = {
                "kind": "app",
                "seat": artifact.author,
                "instance": instance,
                "artifact_id": str(artifact.id),
                "path": artifact.content,
            }
        else:
            path = clean_artifact_path(
                artifact.content if artifact else selection.path or ""
            )
            version = await asyncio.to_thread(
                library.preview_file_version, place.project_id, topic_id, path
            )
            if not version or selection.version != version:
                raise NotFoundError("Preview entry changed or unavailable")
            resource = {
                "kind": "file",
                "path": path,
                "version": version,
                "mime": artifact.mime_type
                if artifact
                else mimetypes.guess_type(path)[0] or "text/html",
            }
    response.headers["Cache-Control"] = "no-store"
    return ok(
        {
            "url": preview_origin(topic_id, resource) + AUTH_PATH,
            "grant": mint_preview_token(
                topic_id,
                actor.handle,
                purpose="preview-grant",
                ttl=GRANT_TTL,
                resource=resource,
            ),
            **(
                {"resource": resource, "resource_id": resource_key(resource)}
                if resource
                else {}
            ),
        }
    )
