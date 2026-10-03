"""Machine task metadata and private backups; Git traffic goes to the forge."""

import asyncio
import tempfile
import uuid
from typing import Annotated, BinaryIO, cast

from fastapi import APIRouter, Depends, Header, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import require_seated_agent
from app.api.response import ok
from app.core.db import get_db
from app.core.errors import AuthenticationRequiredError, NotFoundError, ValidationError
from app.core.sandbox_auth import token_agent_handle, verify_scoped_token
from app.domain.block.notice_text import say

router = APIRouter(prefix="/projects", tags=["git"])


async def _task_for(db, project_id, task_id, token):
    from app.domain.room_task.services import TaskService

    if not token or not verify_scoped_token(token, project_id=str(project_id)):
        raise AuthenticationRequiredError("Task access needs this project's token")
    task = await TaskService(db).get(task_id)
    if (
        task is None
        or task.project_id != project_id
        or not verify_scoped_token(
            token, project_id=str(project_id), topic_id=str(task.room_id)
        )
    ):
        raise NotFoundError(say("workTaskNotInRoom"))
    await require_seated_agent(db, token, project_id=project_id, topic_id=task.room_id)
    return task


@router.put("/{project_id}/git/tasks/{task_id}/snapshots/{snapshot_sha}")
async def save_task_snapshot(
    project_id: uuid.UUID,
    task_id: uuid.UUID,
    snapshot_sha: str,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    x_cheese_token: str | None = Header(default=None),
    x_cheese_head: str = Header(),
    x_content_sha256: str = Header(),
) -> dict:
    from app.domain.room_task import snapshots

    task = await _task_for(db, project_id, task_id, x_cheese_token)
    size = 0
    with tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024) as file:
        async for chunk in request.stream():
            size += len(chunk)
            if size > 512 * 1024 * 1024:
                raise ValidationError(say("taskBackupTooLarge"))
            await asyncio.to_thread(file.write, chunk)
        row = await snapshots.save(
            db,
            task,
            file=cast(BinaryIO, file),
            head_sha=x_cheese_head,
            snapshot_sha=snapshot_sha,
            digest=x_content_sha256,
        )
    return ok(
        {"id": str(row.id), "snapshot_sha": row.snapshot_sha, "digest": row.digest}
    )


@router.get("/{project_id}/git/tasks/{task_id}/snapshots/latest")
async def latest_task_snapshot(
    project_id: uuid.UUID,
    task_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    x_cheese_token: str | None = Header(default=None),
) -> dict:
    from app.domain.room_task import snapshots

    await _task_for(db, project_id, task_id, x_cheese_token)
    row = await snapshots.latest(db, task_id)
    return ok(
        {
            "id": str(row.id),
            "snapshot_sha": row.snapshot_sha,
            "head_sha": row.head_sha,
            "digest": row.digest,
        }
    )


@router.get("/{project_id}/git/tasks/{task_id}/snapshots/{snapshot_id}")
async def download_task_snapshot(
    project_id: uuid.UUID,
    task_id: uuid.UUID,
    snapshot_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    x_cheese_token: str | None = Header(default=None),
) -> Response:
    from app.domain.room_task import snapshots
    from app.domain.room_task.models import TaskSnapshot

    await _task_for(db, project_id, task_id, x_cheese_token)
    row = await db.get(TaskSnapshot, snapshot_id)
    if row is None or row.task_id != task_id:
        raise NotFoundError(say("taskBackupNotFound"))
    return Response(
        await snapshots.download(row),
        media_type="application/x-git-bundle",
        headers={"Cache-Control": "no-store"},
    )


@router.post("/{project_id}/git/tasks/{task_id}")
async def open_task_workspace(
    project_id: uuid.UUID,
    task_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    x_cheese_token: str | None = Header(default=None, alias="X-Cheese-Token"),
) -> dict:
    from app.domain.room_task.services import TaskService

    task = await _task_for(db, project_id, task_id, x_cheese_token)
    acting = token_agent_handle(x_cheese_token or "")
    if not acting:
        raise AuthenticationRequiredError("Opening a task needs an agent identity")
    if task.status == "closed":
        raise ValidationError(say("taskEndedCreateNew"))
    await TaskService(db).record_author(task, acting)
    result = await task_workspace(project_id, task_id, db, x_cheese_token)
    await db.commit()
    return result


@router.get("/{project_id}/git/tasks/{task_id}")
async def task_workspace(
    project_id: uuid.UUID,
    task_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    x_cheese_token: str | None = Header(default=None, alias="X-Cheese-Token"),
) -> dict:
    if not x_cheese_token or not verify_scoped_token(
        x_cheese_token, project_id=str(project_id)
    ):
        raise AuthenticationRequiredError("git access needs this project's token")
    from app.domain.project.forge import binding_for_project
    from app.domain.room_task.services import TaskService

    task = await TaskService(db).get(task_id)
    if task is None or task.project_id != project_id or task.branch_name is None:
        raise NotFoundError(say("workTaskNotInProject"))
    if not verify_scoped_token(
        x_cheese_token or "", project_id=str(project_id), topic_id=str(task.room_id)
    ):
        raise NotFoundError(say("workTaskNotInRoom"))
    await require_seated_agent(
        db, x_cheese_token, project_id=project_id, topic_id=task.room_id
    )
    binding = await binding_for_project(project_id, db)
    if binding is None:
        raise NotFoundError(say("projectHasNoRepo"))
    from app.domain.repository import identity
    from app.domain.topic.services import TopicService

    room = await TopicService(db).get_or_404(task.room_id)
    who = await identity.attribution(db, room, task_id=task.id)
    from app.domain.project.forge import ensure_author_email

    if who.author:
        await ensure_author_email(project_id, db, who.author.email)
    from app.domain.review.services import AcceptService

    # A queued PR's branch is locked by the forge until the queue merges or
    # drops it; a machine that pushes to it is refused. It is told here so it
    # can say that, instead of reporting a failed push to be retried.
    queued = await AcceptService(db).merge_queued_pr(task.id)
    return ok(
        {
            "task_id": str(task.id),
            "room_id": str(task.room_id),
            "branch": task.branch_name,
            "base": task.base_branch,
            "closed": task.status == "closed",
            "merge_queued_pr": queued,
            "remote": binding.url,
            "forge_kind": binding.kind,
            "forge_repo": binding.repo,
            "author": str(who.author) if who.author else None,
            "coauthors": [str(person) for person in who.coauthors],
        }
    )
