"""Workspace routes — files, git log, git diff (Phase 4 执行面板)."""

import uuid
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.response import ok, page
from app.core.config import settings
from app.core.db import get_db
from app.domain.project.services import ProjectService
from app.domain.workspace import service as ws

router = APIRouter(prefix="/api/projects", tags=["workspace"])

DbSession = Annotated[AsyncSession, Depends(get_db)]


def _remote() -> bool:
    """Files live on the cheesed node when compute runs remotely (R9 read-back)."""
    return settings.compute_provider == "remote"


@router.get("/{project_id}/files")
async def list_files(
    project_id: uuid.UUID, db: DbSession, topic: uuid.UUID | None = None
) -> dict:
    await ProjectService(db).get_or_404(project_id)
    if _remote() and topic is not None:
        url = f"{settings.cheesed_url.rstrip('/')}/files/{project_id}/{topic}"
        async with httpx.AsyncClient(timeout=15) as client:
            files = (await client.get(url)).json().get("data", [])
    else:
        # Files live in the topic's worktree; without a topic the base repo is empty.
        files = ws.list_files(project_id, topic_id=topic)
    return ok(page(files, len(files)))


@router.get("/{project_id}/file")
async def read_file(
    project_id: uuid.UUID, path: str, db: DbSession, topic: uuid.UUID | None = None
) -> dict:
    await ProjectService(db).get_or_404(project_id)
    if _remote() and topic is not None:
        url = f"{settings.cheesed_url.rstrip('/')}/file/{project_id}/{topic}"
        async with httpx.AsyncClient(timeout=15) as client:
            content = (await client.get(url, params={"path": path})).json().get("data")
    else:
        content = ws.read_file(project_id, path, topic_id=topic)
    return ok({"path": path, "content": content})


@router.get("/{project_id}/git/log")
async def git_log(project_id: uuid.UUID, db: DbSession) -> dict:
    await ProjectService(db).get_or_404(project_id)
    rows = ws.git_log(project_id)
    return ok(page(rows, len(rows)))


@router.get("/{project_id}/git/diff")
async def git_diff(
    project_id: uuid.UUID,
    db: DbSession,
    ref: str | None = None,
    topic: uuid.UUID | None = None,
) -> dict:
    await ProjectService(db).get_or_404(project_id)
    # A topic shows its branch's full diff vs the base (what 采纳 would merge).
    if topic is not None:
        return ok({"diff": ws.topic_diff(project_id, topic)})
    return ok({"diff": ws.git_diff(project_id, ref)})
