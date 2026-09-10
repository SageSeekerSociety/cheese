"""Workspace routes — files, git log, git diff (Phase 4 执行面板)."""

import mimetypes
import uuid
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, Header, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import ActorResolverDep
from app.api.response import ok, page
from app.auth.caller import may_access_project
from app.core.db import get_db
from app.core.errors import NotFoundError, ValidationError
from app.core.sandbox_auth import verify_scoped_token
from app.domain.agent_session.services import AgentSessionService
from app.domain.project.services import ProjectService
from app.domain.room_task.models import TaskStatus
from app.domain.room_task.services import TaskService
from app.domain.topic.models import TopicStatus
from app.domain.topic.services import TopicService
from app.domain.workspace import service as ws

router = APIRouter(prefix="/projects", tags=["workspace"])

DbSession = Annotated[AsyncSession, Depends(get_db)]


async def require_project_access(
    project_id: uuid.UUID,
    request: Request,
    db: DbSession,
    resolver: ActorResolverDep,
    topic: uuid.UUID | None = None,
    task: uuid.UUID | None = None,
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
    scoped = bool(x_cheese_token) and verify_scoped_token(
        x_cheese_token or "", project_id=str(project_id)
    )
    if not scoped and not await may_access_project(request, db, project_id):
        raise NotFoundError("project not found")

    # Files and Git diffs contain the private room's source, not just its title.
    # The path-bound room wins over a query parameter on work-summary requests.
    room_id = request.path_params.get("topic_id") or topic
    work = await TaskService(db).get(task) if task is not None else None
    if task is not None:
        if work is None or work.project_id != project_id:
            raise NotFoundError("Task not found")
        if room_id is not None and str(work.room_id) != str(room_id):
            raise NotFoundError("Task not found")
        room_id = work.room_id
    if room_id is not None:
        try:
            room_uuid = uuid.UUID(str(room_id))
        except ValueError as exc:
            raise NotFoundError("Topic not found") from exc
        room = await TopicService(db).get_or_404(room_uuid)
        if room.project_id != project_id:
            raise NotFoundError("Topic not found")
        actor = await resolver.resolve(
            fallback_handle=None, project_id=project_id, topic_id=room.id
        )
        await resolver.authorize_topic(actor, project_id=project_id, topic_id=room.id)
        if request.method == "PUT" and room.status == TopicStatus.archived:
            raise ValidationError("房间已归档，文件只读")
    if work is not None:
        if request.method == "PUT" and work.status != TaskStatus.open:
            raise ValidationError("任务已结束，文件只读")
        if work.branch_name is None:
            raise NotFoundError("Historical task has no individual workspace")
        TaskService._bind_workspace(work)


@router.get("/{project_id}/files", dependencies=[Depends(require_project_access)])
async def list_files(
    project_id: uuid.UUID,
    db: DbSession,
    topic: uuid.UUID | None = None,
    task: uuid.UUID | None = None,
) -> dict:
    await ProjectService(db).get_or_404(project_id)
    # A selected task owns its worktree; otherwise show the project base.
    files = ws.list_files(project_id, topic_id=task)
    return ok(page(files, len(files)))


@router.get("/{project_id}/file", dependencies=[Depends(require_project_access)])
async def read_file(
    project_id: uuid.UUID,
    path: str,
    db: DbSession,
    topic: uuid.UUID | None = None,
    task: uuid.UUID | None = None,
) -> dict:
    """Read a worktree file for the 文件 panel.

    The payload carries more than the text: `binary` / `too_large` tell the panel
    to render a read-only view (a text editor would corrupt the file on save, and
    a 52MB file would never have made it through the browser anyway), and
    `version` is what a later write echoes back so a lost race is caught.
    """
    await ProjectService(db).get_or_404(project_id)
    return ok(ws.read_text_file(project_id, path, topic_id=task))


@router.get("/{project_id}/file/raw", dependencies=[Depends(require_project_access)])
async def read_file_raw(
    project_id: uuid.UUID,
    path: str,
    db: DbSession,
    topic: uuid.UUID | None = None,
    task: uuid.UUID | None = None,
    download: bool = False,
) -> Response:
    """Raw bytes of a worktree file — the 文件 panel renders images as images
    (the text endpoint would mangle binary content)."""
    await ProjectService(db).get_or_404(project_id)
    data = ws.read_file_bytes(project_id, path, topic_id=task)
    mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
    # Arbitrary uploads must never execute in the app's origin.
    download = download or not mime.startswith("image/")
    headers = {
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'none'; sandbox",
    }
    if download:
        headers["Content-Disposition"] = (
            f"attachment; filename*=UTF-8''{quote(path.rsplit('/', 1)[-1], safe='')}"
        )
    return Response(
        content=data,
        media_type="application/octet-stream" if download else mime,
        headers=headers,
    )


@router.put("/{project_id}/file", dependencies=[Depends(require_project_access)])
async def write_file(
    project_id: uuid.UUID,
    body: dict,
    db: DbSession,
    topic: uuid.UUID | None = None,
    task: uuid.UUID | None = None,
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
    if task is None:
        raise ValidationError("请选择要修改的任务")
    new_version = ws.write_file(
        project_id, path, content, topic_id=task, expected_version=version
    )
    return ok({"path": path, "version": new_version})


@router.get("/{project_id}/git/log", dependencies=[Depends(require_project_access)])
async def git_log(
    project_id: uuid.UUID,
    db: DbSession,
    topic: uuid.UUID | None = None,
    task: uuid.UUID | None = None,
) -> dict:
    await ProjectService(db).get_or_404(project_id)
    # A topic asks about ITS commits (its branch minus the base), never the
    # project's — the project log is other topics' work.
    rows = ws.git_log(project_id, topic_id=task)
    return ok(page(rows, len(rows)))


@router.get("/{project_id}/git/diff", dependencies=[Depends(require_project_access)])
async def git_diff(
    project_id: uuid.UUID,
    db: DbSession,
    ref: str | None = None,
    topic: uuid.UUID | None = None,
    task: uuid.UUID | None = None,
) -> dict:
    await ProjectService(db).get_or_404(project_id)
    # A topic shows its branch's full diff vs the base (what 采纳 would merge).
    if task is not None:
        return ok({"diff": ws.topic_diff(project_id, task)})
    if ref:
        ref = ws.accepted_commit_revision(project_id, ref)
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

    ``changed_files`` combines the open tasks' changed paths for the room's
    badge. The changes panel selects one task before showing its diff.
    """
    await ProjectService(db).get_or_404(project_id)
    place = await TopicService(db).place_or_404(topic_id)
    tasks = await TaskService(db).list_in_room(place.room_id)
    paths = set()
    for work in tasks:
        if work.branch_name and work.status == "open":
            TaskService._bind_workspace(work)
            paths.update(ws.topic_changed_files(project_id, work.id))
    # 跑过没有 = 这个地点有没有哪个 agent 留下过会话。
    has_run = await AgentSessionService(db).has_run(place.room_id)
    return ok({"changed_files": sorted(paths), "has_run": has_run})
