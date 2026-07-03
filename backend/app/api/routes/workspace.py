"""Workspace routes — files, git log, git diff (Phase 4 执行面板)."""

import mimetypes
import uuid
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.response import ok, page
from app.core.config import settings
from app.core.db import get_db
from app.core.errors import ValidationError
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


@router.get("/{project_id}/file/raw")
async def read_file_raw(
    project_id: uuid.UUID, path: str, db: DbSession, topic: uuid.UUID | None = None
) -> Response:
    """Raw bytes of a worktree file — the 文件 panel renders images as images
    (the text endpoint would mangle binary content)."""
    await ProjectService(db).get_or_404(project_id)
    data = ws.read_file_bytes(project_id, path, topic_id=topic)
    mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
    return Response(content=data, media_type=mime)


@router.put("/{project_id}/file")
async def write_file(
    project_id: uuid.UUID,
    body: dict,
    db: DbSession,
    topic: uuid.UUID | None = None,
) -> dict:
    """Save an edited workspace file (人改文件即指令). Writes to the topic's
    worktree, or proxies to the cheesed node when compute runs remotely."""
    await ProjectService(db).get_or_404(project_id)
    path = (body.get("path") or "").strip()
    content = body.get("content") or ""
    if not path:
        raise ValidationError("path is required")
    if _remote() and topic is not None:
        url = f"{settings.cheesed_url.rstrip('/')}/file/{project_id}/{topic}"
        async with httpx.AsyncClient(timeout=15) as client:
            await client.put(url, json={"path": path, "content": content})
    else:
        ws.write_file(project_id, path, content, topic_id=topic)
    return ok({"path": path})


@router.get("/{project_id}/git/log")
async def git_log(
    project_id: uuid.UUID, db: DbSession, topic: uuid.UUID | None = None
) -> dict:
    await ProjectService(db).get_or_404(project_id)
    if _remote() and topic is not None:
        url = f"{settings.cheesed_url.rstrip('/')}/git/log/{project_id}/{topic}"
        async with httpx.AsyncClient(timeout=15) as client:
            rows = (await client.get(url)).json().get("data", [])
    else:
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
    if _remote() and topic is not None:
        url = f"{settings.cheesed_url.rstrip('/')}/git/diff/{project_id}/{topic}"
        async with httpx.AsyncClient(timeout=15) as client:
            return ok({"diff": (await client.get(url)).json().get("data", "")})
    # A topic shows its branch's full diff vs the base (what 采纳 would merge).
    if topic is not None:
        return ok({"diff": ws.topic_diff(project_id, topic)})
    return ok({"diff": ws.git_diff(project_id, ref)})
