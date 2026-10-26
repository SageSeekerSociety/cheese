"""Workspace routes — files, git log, git diff (Phase 4 执行面板)."""

import asyncio
import mimetypes
import uuid
from typing import Annotated, Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, Header, Request, Response

from app.api.auth import ActorResolverDep, require_seated_in_its_room
from app.api.deps import get_chat_service
from app.api.doc_access import require_role
from app.api.request_deps import DbSession
from app.api.response import ok, page
from app.api.write_access import ROUTE_DECIDES
from app.auth.project_access import may_read_project
from app.core.errors import (
    AuthenticationRequiredError,
    GatewayUnavailableError,
    NotFoundError,
    ValidationError,
)
from app.core.sandbox_auth import verify_scoped_token
from app.core.sentences import say
from app.domain.agent import execution
from app.domain.agent.chat import ChatService
from app.domain.agent.file_edits import announce_edit
from app.domain.agent_session.services import AgentSessionService
from app.domain.project.services import ProjectService
from app.domain.project_permissions import Permission
from app.domain.repository.forge_files import ProjectFiles
from app.domain.review import forge_clients
from app.domain.room_task.place import Task, TaskStatus, TopicStatus, place_or_404
from app.domain.room_task.services import TaskService
from app.domain.topic.services import TopicService

router = APIRouter(prefix="/projects", tags=["workspace"])


async def _may_read(resolver, db, project_id: uuid.UUID) -> bool:
    """Whether the person this request acts as may read the project's
    conversations (`app.auth.project_access`). A sign-in that was presented and
    did not verify is a 401, not 「no such project」: the browser refreshes on a
    401 and has nothing to do with a 404."""
    person = await resolver.person()
    if person is None and resolver.presented_sign_in():
        raise AuthenticationRequiredError(say("sessionExpired"))
    return await may_read_project(
        db, project_id=project_id, handle=person.handle if person else None
    )


async def require_place_access(
    project_id: uuid.UUID,
    request: Request,
    db: DbSession,
    resolver: ActorResolverDep,
    topic: uuid.UUID | None = None,
    task: uuid.UUID | None = None,
    x_cheese_token: str | None = Header(default=None, alias="X-Cheese-Token"),
) -> Task | None:
    """Reject a caller with no claim on this project; answer the task it names.

    These routes return the source itself and checked only that the project
    EXISTS — not a check on the caller, so an unauthenticated request carrying a
    project id was served the file list and file contents.

    Both legitimate callers keep working: a human in the browser (member or
    owner, resolved by HANDLE — see app.auth.caller.handle_named_by for why
    user id is not enough) and the agent on a machine (its project-scoped token). Anything else
    gets NotFound, so the answer does not confirm the project exists.

    A route dependency rather than a line in each handler: the per-handler shape
    is exactly how six routes came to share one hole.
    """
    scoped = bool(x_cheese_token) and verify_scoped_token(
        x_cheese_token or "", project_id=str(project_id)
    )
    if not scoped and not await _may_read(resolver, db, project_id):
        raise NotFoundError(say("projectNotFound"))

    # Files and Git diffs contain the private room's source, not just its title.
    # The path-bound room wins over a query parameter on work-summary requests.
    room_id = request.path_params.get("topic_id") or topic
    # A task's conversation in the path is that task, in its room.
    if task is None and room_id is not None:
        try:
            named = await TaskService(db).get(uuid.UUID(str(room_id)))
        except ValueError:
            named = None
        if named is not None:
            task, room_id = named.id, None
    work = await TaskService(db).get(task) if task is not None else None
    if task is not None:
        if work is None or work.project_id != project_id:
            raise NotFoundError(say("taskNotFound"))
        if room_id is not None and str(work.room_id) != str(room_id):
            raise NotFoundError(say("taskNotFoundInRoom"))
        room_id = work.room_id
    if scoped and room_id is None:
        # The whole project's source, to an agent that still sits in the room
        # its credential was minted in.
        await require_seated_in_its_room(
            db, x_cheese_token or "", project_id=project_id
        )
    if room_id is not None:
        try:
            room_uuid = uuid.UUID(str(room_id))
        except ValueError as exc:
            raise NotFoundError(say("topicNotFound")) from exc
        room = await TopicService(db).get_or_404(room_uuid)
        if room.project_id != project_id:
            raise NotFoundError(say("topicNotFound"))
        actor = await resolver.resolve(project_id=project_id, topic_id=room.id)
        await resolver.authorize_topic(actor, project_id=project_id, topic_id=room.id)
        if request.method == "PUT" and room.status == TopicStatus.archived:
            raise ValidationError(say("roomArchivedFilesReadOnly"))
    if work is not None and request.method == "PUT" and work.status != TaskStatus.open:
        raise ValidationError(say("taskEndedFilesReadOnly"))
    return work


async def require_project_access(
    work: Annotated[Task | None, Depends(require_place_access)],
) -> None:
    """Access, plus a task's own worktree for the routes that read and write it.

    A task that arrived without a branch gets one only when a machine first
    opens it (`give_branch`), so until then it has no files of its own to serve.
    Asking what work it holds is not reading them: the work summary takes
    `require_place_access` alone and answers "nothing changed yet".
    """
    if work is not None and work.branch_name is None:
        raise NotFoundError(say("taskNoOwnFileVersion"))


@router.get("/{project_id}/files", dependencies=[Depends(require_project_access)])
async def list_files(
    project_id: uuid.UUID,
    db: DbSession,
    topic: uuid.UUID | None = None,
    task: uuid.UUID | None = None,
    source: Literal["live", "committed"] = "live",
) -> dict:
    await ProjectService(db).get_or_404(project_id)
    # A selected task owns its worktree; otherwise show the project base.
    files, actual_source = await ProjectFiles(
        db, project_id, task, release_session=True, executor=execution.call
    ).files(source)
    return ok({**page(files, len(files)), "source": actual_source})


@router.get("/{project_id}/file", dependencies=[Depends(require_project_access)])
async def read_file(
    project_id: uuid.UUID,
    path: str,
    db: DbSession,
    topic: uuid.UUID | None = None,
    task: uuid.UUID | None = None,
    source: Literal["live", "committed"] = "live",
) -> dict:
    """Read a worktree file for the 文件 panel.

    The payload carries more than the text: `binary` / `too_large` tell the panel
    to render a read-only view (a text editor would corrupt the file on save, and
    a 52MB file would never have made it through the browser anyway), and
    `version` is what a later write echoes back so a lost race is caught.
    """
    await ProjectService(db).get_or_404(project_id)
    return ok(
        await ProjectFiles(
            db, project_id, task, release_session=True, executor=execution.call
        ).text(path, source)
    )


@router.get("/{project_id}/file/raw", dependencies=[Depends(require_project_access)])
async def read_file_raw(
    project_id: uuid.UUID,
    path: str,
    db: DbSession,
    topic: uuid.UUID | None = None,
    task: uuid.UUID | None = None,
    download: bool = False,
    source: Literal["live", "committed"] = "live",
) -> Response:
    """Raw bytes of a worktree file — the 文件 panel renders images as images
    (the text endpoint would mangle binary content)."""
    await ProjectService(db).get_or_404(project_id)
    data, actual_source = await ProjectFiles(
        db, project_id, task, release_session=True, executor=execution.call
    ).raw(path, source)
    mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
    # Arbitrary uploads must never execute in the app's origin.
    download = download or not mime.startswith("image/")
    headers = {
        "X-Cheese-File-Source": actual_source,
        "Cache-Control": "no-store",
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


@router.put(
    "/{project_id}/file", dependencies=[ROUTE_DECIDES, Depends(require_project_access)]
)
async def write_file(
    project_id: uuid.UUID,
    body: dict,
    db: DbSession,
    resolver: ActorResolverDep,
    chat: Annotated[ChatService, Depends(get_chat_service)],
    topic: uuid.UUID | None = None,
    task: uuid.UUID | None = None,
) -> dict:
    """Save an edited workspace file (人改文件即指令). Writes to the topic's
    worktree.

    `version` is the one the caller read and `base` the text it read. If the
    file moved on in between, the two sets of edits are merged against `base`:
    a clean merge is saved, overlapping edits answer 409 with the regions to
    pick from. A person's save is then said in the task, and the task's AI
    teammate is told before its next tool call to re-read the file.
    """
    await ProjectService(db).get_or_404(project_id)
    await require_role(
        db,
        await resolver.resolve(project_id=project_id),
        project_id,
        Permission.edit_content,
    )
    path = (body.get("path") or "").strip()
    content = body.get("content") or ""
    version = body.get("version") or None
    base = body.get("base")
    if not path:
        raise ValidationError(say("pathEmpty"))
    if task is None:
        raise ValidationError(say("chooseTaskToEdit"))
    actor = await resolver.resolve(project_id=project_id, topic_id=task)
    saved = await ProjectFiles(
        db, project_id, task, release_session=True, executor=execution.call
    ).save(path, content, version, base if isinstance(base, str) else None)
    row = await TaskService(db).get(task)
    if row is not None and actor.authenticated and actor.via != "cheese":
        said = await announce_edit(
            db,
            task=row,
            who=actor.handle,
            path=path,
            previous=saved["previous"],
            content=saved.get("content", content),
        )
        await db.commit()
        if said is not None:
            line, told = said
            await chat.notify_running_session(row.id, told, blocks=[line.id])
    return ok(
        {
            "path": path,
            "version": saved["version"],
            "source": "live",
            "merged": saved["merged"],
            **({"content": saved["content"]} if saved["merged"] else {}),
        }
    )


@router.get("/{project_id}/git/diff", dependencies=[Depends(require_project_access)])
async def git_diff(
    project_id: uuid.UUID,
    db: DbSession,
    ref: str | None = None,
    topic: uuid.UUID | None = None,
    task: uuid.UUID | None = None,
    source: Literal["live", "committed"] = "committed",
) -> dict:
    await ProjectService(db).get_or_404(project_id)
    return ok(
        {
            "diff": await ProjectFiles(
                db,
                project_id,
                task,
                release_session=True,
                executor=execution.call,
                compare_status=forge_clients.compare_status,
            ).diff(source, ref)
        }
    )


@router.get(
    "/{project_id}/topics/{topic_id}/work-summary",
    dependencies=[Depends(require_place_access)],
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

    ``changed_files`` is a task's own changed paths, for the badge on its 改动
    tab. A channel has no 改动 tab (changes belong to tasks and are read on the
    task's page, #2422), so it answers none and compares no branch: comparing
    every open task in a busy channel took past the timeout on every visit.
    """
    await ProjectService(db).get_or_404(project_id)
    place = await place_or_404(db, topic_id)
    one = (
        await TaskService(db).get(place.task_id) if place.task_id is not None else None
    )
    has_run = await AgentSessionService(db).has_run(place.conversation_id)
    paths: list[str] = []
    if one is not None and one.branch_name and one.status == "open":
        try:
            async with asyncio.timeout(15):
                paths = await ProjectFiles(
                    db, project_id, one.id, release_session=True
                ).changed_files()
        except TimeoutError as exc:
            raise GatewayUnavailableError(say("changeSummaryTimeout")) from exc
    return ok({"changed_files": sorted(paths), "has_run": has_run})
