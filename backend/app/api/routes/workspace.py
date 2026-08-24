"""Workspace routes — files, git log, git diff (Phase 4 执行面板)."""

import mimetypes
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.response import ok, page
from app.auth.caller import may_access_project
from app.core.db import get_db
from app.core.errors import NotFoundError, ValidationError
from app.core.sandbox_auth import verify_scoped_token
from app.domain.agent_session.services import AgentSessionService
from app.domain.project.services import ProjectService
from app.domain.topic.services import TopicService
from app.domain.workspace import service as ws

router = APIRouter(prefix="/projects", tags=["workspace"])

DbSession = Annotated[AsyncSession, Depends(get_db)]


async def require_project_access(
    project_id: uuid.UUID,
    request: Request,
    db: DbSession,
    x_cheese_token: str | None = Header(default=None, alias="X-Cheese-Token"),
) -> None:
    """Reject a caller with no claim on this project.

    These routes return the source itself and checked only that the project
    EXISTS — not a check on the caller, so an unauthenticated request carrying a
    project id was served the file list and file contents.

    Both legitimate callers keep working: a human in the browser (member or
    owner, resolved by HANDLE — see app.auth.caller for why user id is not
    enough) and the agent on a machine (its project-scoped token). Anything else
    gets NotFound, so the answer does not confirm the project exists.

    A route dependency rather than a line in each handler: the per-handler shape
    is exactly how six routes came to share one hole.
    """
    if x_cheese_token and verify_scoped_token(
        x_cheese_token, project_id=str(project_id)
    ):
        return
    if await may_access_project(request, db, project_id):
        return
    raise NotFoundError("project not found")


@router.get("/{project_id}/files", dependencies=[Depends(require_project_access)])
async def list_files(
    project_id: uuid.UUID, db: DbSession, topic: uuid.UUID | None = None
) -> dict:
    await ProjectService(db).get_or_404(project_id)
    # Files live in the topic's worktree; without a topic the base repo is empty.
    files = ws.list_files(project_id, topic_id=topic)
    return ok(page(files, len(files)))


@router.get("/{project_id}/file", dependencies=[Depends(require_project_access)])
async def read_file(
    project_id: uuid.UUID, path: str, db: DbSession, topic: uuid.UUID | None = None
) -> dict:
    """Read a worktree file for the 文件 panel.

    The payload carries more than the text: `binary` / `too_large` tell the panel
    to render a read-only view (a text editor would corrupt the file on save, and
    a 52MB file would never have made it through the browser anyway), and
    `version` is what a later write echoes back so a lost race is caught.
    """
    await ProjectService(db).get_or_404(project_id)
    return ok(ws.read_text_file(project_id, path, topic_id=topic))


@router.get("/{project_id}/file/raw", dependencies=[Depends(require_project_access)])
async def read_file_raw(
    project_id: uuid.UUID, path: str, db: DbSession, topic: uuid.UUID | None = None
) -> Response:
    """Raw bytes of a worktree file — the 文件 panel renders images as images
    (the text endpoint would mangle binary content)."""
    await ProjectService(db).get_or_404(project_id)
    data = ws.read_file_bytes(project_id, path, topic_id=topic)
    mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
    return Response(content=data, media_type=mime)


@router.put("/{project_id}/file", dependencies=[Depends(require_project_access)])
async def write_file(
    project_id: uuid.UUID,
    body: dict,
    db: DbSession,
    topic: uuid.UUID | None = None,
) -> dict:
    """Save an edited workspace file (人改文件即指令). Writes to the topic's
    worktree.

    `version` is the one the caller read. Sending it makes the write conditional:
    if 芝士 (or anyone else) wrote the file in between, the save is rejected with
    409 instead of silently erasing their work, and the panel shows the conflict.
    """
    await ProjectService(db).get_or_404(project_id)
    path = (body.get("path") or "").strip()
    content = body.get("content") or ""
    version = body.get("version") or None
    if not path:
        raise ValidationError("path is required")
    new_version = ws.write_file(
        project_id, path, content, topic_id=topic, expected_version=version
    )
    return ok({"path": path, "version": new_version})


@router.get("/{project_id}/git/log", dependencies=[Depends(require_project_access)])
async def git_log(
    project_id: uuid.UUID, db: DbSession, topic: uuid.UUID | None = None
) -> dict:
    await ProjectService(db).get_or_404(project_id)
    # A topic asks about ITS commits (its branch minus the base), never the
    # project's — the project log is other topics' work.
    rows = ws.git_log(project_id, topic_id=topic)
    return ok(page(rows, len(rows)))


@router.get("/{project_id}/git/diff", dependencies=[Depends(require_project_access)])
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


@router.get(
    "/{project_id}/topics/{topic_id}/work-summary",
    dependencies=[Depends(require_project_access)],
)
async def topic_work_summary(
    project_id: uuid.UUID, topic_id: uuid.UUID, db: DbSession
) -> dict:
    """What work this topic is holding — asked while the panels are CLOSED.

    The 工作面板 offers a tab only where the thing it shows exists, and puts the
    change count on 改动 without opening it. Both are facts about tabs nobody is
    looking at, so both have to be answerable without fetching the thing itself:
    pulling a whole diff to arrive at one integer is the shape that got the 资源
    drawer's 20-second poll deleted.

    ``has_run`` is the topic's captured session, not its message count: 现场
    shows what 芝士 did, and a room where only people talked has no 现场 to open.
    """
    await ProjectService(db).get_or_404(project_id)
    await TopicService(db).get_or_404(topic_id)
    paths = ws.topic_changed_files(project_id, topic_id)
    # 跑过没有 = 这里有没有哪个 agent 留下过会话。
    has_run = await AgentSessionService(db).has_run(topic_id)
    return ok({"changed_files": paths, "has_run": has_run})
