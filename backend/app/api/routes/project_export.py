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
from app.core.db import get_db
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
    archive, _ = await create_archive(project_id, db, resolver)
    return ProjectArchiveResponse(
        archive,
        media_type="application/x-tar",
        filename=f"project-{project_id}.tar",
        headers={"Cache-Control": "no-store"},
    )
