"""Download a project without needing the UI."""

import shutil
import uuid
from pathlib import Path
from typing import Annotated

from anyio import CancelScope
from anyio.to_thread import run_sync
from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.types import Receive, Scope, Send

from app.api.auth import ActorResolverDep
from app.auth.project_access import may_read_project
from app.core.db import get_db
from app.core.errors import AuthenticationRequiredError, ForbiddenError
from app.domain.identity.services import IdentityService
from app.domain.project.export import create_archive


class ProjectArchiveResponse(FileResponse):
    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            # Include interrupted downloads: background tasks only run after send.
            with CancelScope(shield=True):
                await run_sync(shutil.rmtree, Path(self.path).parent)


router = APIRouter(prefix="/projects", tags=["projects"])


@router.get("/{project_id}/export")
async def export_project(
    project_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    resolver: ActorResolverDep,
) -> FileResponse:
    # Who is asking, and what they may read, is answered here: this layer owns
    # the credential, and the archive builder is handed the answers. It gets
    # the resolver only for the last question — which of the project's rooms
    # this caller reads — because the room list is in hand there.
    actor = await resolver.resolve(project_id=project_id)
    if actor.via != "token" or actor.user_id is None:
        raise AuthenticationRequiredError("Project export requires a human login")
    if await IdentityService(db).is_agent(actor.handle) or not await may_read_project(
        db, project_id=project_id, handle=actor.handle
    ):
        raise ForbiddenError("Project export requires project access")
    archive, _ = await create_archive(project_id, db, actor, resolver)
    return ProjectArchiveResponse(
        archive,
        media_type="application/x-tar",
        filename=f"project-{project_id}.tar",
        headers={"Cache-Control": "no-store"},
    )
