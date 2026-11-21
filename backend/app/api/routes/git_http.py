"""Machine task metadata; Git traffic goes to the forge."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import PresentedCredential, require_seated_agent
from app.api.response import ok
from app.api.write_access import ROUTE_DECIDES, Can
from app.core.db import get_db
from app.core.errors import (
    AuthenticationRequiredError,
    BadRequestError,
    ForbiddenError,
    NotFoundError,
    ValidationError,
)
from app.core.sandbox_auth import (
    scoped_token_claims,
    token_agent_handle,
    verify_scoped_token,
)
from app.core.sentences import say
from app.domain.agent.announce import announce
from app.domain.agent_instance.adapters import project_team, task_teammate
from app.domain.delivery.agent import (
    record_task_instruction,
)
from app.domain.delivery.ledger import settle_card
from app.domain.project_permissions import Permission
from app.domain.repository import kept_work
from app.domain.review import credit
from app.domain.review.services import AcceptService
from app.domain.topic.services import TopicService

router = APIRouter(prefix="/projects", tags=["git"])


async def _task_for(db, project_id, task_id, token, *, opening: bool = False):
    """The task a credential reaches here, and how.

    Its own session works it: a credential minted in the task's conversation.
    A session of its room reaches it only to read what a machine switch needs
    for every task directory there — the task and its worktree — and never
    opens it: the task is worked in its own conversation, by its owner and that
    session, and nowhere else.
    """
    # deferred-import: tests patch this name on app.domain.room_task.services
    from app.domain.room_task.services import TaskService

    if not token or not verify_scoped_token(token, project_id=str(project_id)):
        raise AuthenticationRequiredError("Task access needs this project's token")
    task = await TaskService(db).get(task_id)
    if task is None or task.project_id != project_id:
        raise NotFoundError(say("workTaskNotInProject"))
    conversation = (scoped_token_claims(token) or {}).get("t")
    if conversation == str(task.id):
        return task
    if conversation != str(task.room_id):
        raise NotFoundError(say("workTaskNotInRoom"))
    await require_seated_agent(db, token, project_id=project_id, topic_id=task.room_id)
    if opening:
        raise ForbiddenError(say("taskWorkedInItsConversation"))
    return task


@router.post("/{project_id}/git/tasks/{task_id}", dependencies=[ROUTE_DECIDES])
async def open_task_workspace(
    project_id: uuid.UUID,
    task_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    credential: PresentedCredential,
) -> dict:
    # deferred-import: tests patch this name on app.domain.room_task.services
    from app.domain.room_task.services import TaskService

    task = await _task_for(db, project_id, task_id, credential, opening=True)
    if task.status == "closed":
        raise ValidationError(say("taskEndedCreateNew"))
    # An agent opening its task is recorded as its author. A command a
    # person or the environment itself started names no agent, and records
    # nobody: a person working the files is not the task's author.
    acting = token_agent_handle(credential)
    if acting:
        await TaskService(db).record_author(task, acting)
    if task.branch_name is None and not await TaskService(db).give_branch(task):
        # deferred-import: tests patch this name on app.domain.project.forge
        from app.domain.project.forge import binding_for_project

        # A task made while the project had no default branch is worked
        # without a checkout until it has one; its session's launch reads this
        # answer as that (`task_checkout`), not as a failure to retry.
        await db.commit()
        raise NotFoundError(
            say("taskWithoutRepository")
            if await binding_for_project(project_id, db) is None
            else say("forgeNoDefaultBranch")
        )
    result = await task_workspace(project_id, task_id, db, credential)
    await db.commit()
    return result


@router.get("/{project_id}/git/tasks/{task_id}")
async def task_workspace(
    project_id: uuid.UUID,
    task_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    credential: PresentedCredential,
) -> dict:
    # deferred-import: tests patch this name on app.domain.project.forge
    from app.domain.project.forge import binding_for_project

    task = await _task_for(db, project_id, task_id, credential)
    binding = await binding_for_project(project_id, db)
    if binding is None:
        raise NotFoundError(say("taskWithoutRepository"))
    if task.branch_name is None:
        # Opening the task (POST) gives it its branch.
        raise NotFoundError(say("taskNoOwnFileVersion"))

    room = await TopicService(db).get_or_404(task.room_id)
    # Who the task's commits are by, and who they credit: the same rule the
    # squash that lands them follows (`identity.Attribution`).
    who = await credit.attribution(db, room, task_id=task.id)
    author = who.commit_author

    # deferred-import: tests patch this name on app.domain.project.forge
    from app.domain.project.forge import ensure_author_email

    if author and binding.kind == "forgejo":
        await ensure_author_email(project_id, db, author.email)

    # A queued PR's branch is locked by the forge until the queue merges or
    # drops it; a machine that pushes to it is refused. It is told here so it
    # can say that, instead of reporting a failed push to be retried.
    queued = await AcceptService(
        db,
        announce=announce,
        settle_card=settle_card,
        record_instruction=record_task_instruction,
        task_teammate=task_teammate,
        roster_team=project_team,
    ).merge_queued_pr(task.id)
    return ok(
        {
            "task_id": str(task.id),
            "room_id": str(task.room_id),
            "branch": task.branch_name,
            "base": task.base_branch,
            # The last head of this task that landed: what a checkout still on
            # an earlier branch carries over from (`cheese worktree`).
            "landed": task.delivered_head,
            "closed": task.status == "closed",
            "merge_queued_pr": queued,
            "remote": binding.url,
            "forge_kind": binding.kind,
            "forge_repo": binding.repo,
            "author": str(author) if author else None,
            "coauthors": [str(person) for person in who.commit_coauthors],
        }
    )


# A task's uncommitted work, kept by the platform for a repository that is not
# its own (`repository.kept_work`): the machine hands its snapshot in as a git
# bundle and a new checkout fetches it back. Reading it takes what reading the
# task's metadata takes (`_task_for`). Writing it changes the task's files: it
# is asked in the task itself and needs what changing the project's files
# needs, so only the task's own environment writes it — its session, or a
# person's command run there, on the environment's authority.


@router.get("/{project_id}/git/tasks/{task_id}/kept")
async def task_kept_bundle(
    project_id: uuid.UUID,
    task_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    credential: PresentedCredential,
) -> Response:
    """The task's snapshot as a bundle; 204 when nothing uncommitted is kept."""
    task = await _task_for(db, project_id, task_id, credential)
    bundle = await kept_work.bundle_of(db, task.id)
    if bundle is None:
        return Response(status_code=204)
    return Response(content=bundle, media_type="application/octet-stream")


@router.put(
    "/{project_id}/git/tasks/{task_id}/kept",
    dependencies=[Can(Permission.edit_content, at="task_id")],
)
async def keep_task_bundle(
    project_id: uuid.UUID,
    task_id: uuid.UUID,
    cut_from: str,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    credential: PresentedCredential,
) -> dict:
    """Keep the bundle sent as the task's snapshot, cut from the commit
    ``cut_from``, in place of the last one."""
    task = await _task_for(db, project_id, task_id, credential)
    bundle = bytearray()
    async for chunk in request.stream():
        bundle.extend(chunk)
        if len(bundle) > kept_work.BUNDLE_MAX_BYTES:
            # i18n-exempt: only the platform's own CLI sends this; no person reads it
            raise BadRequestError("kept bundle too large")
    try:
        row = await kept_work.keep_bundle(db, task.id, bytes(bundle), cut_from)
    except kept_work.NotABundle as exc:
        raise BadRequestError(str(exc)) from exc
    return ok({"commit": row.commit, "cut_from": row.cut_from, "size": row.size})


@router.delete(
    "/{project_id}/git/tasks/{task_id}/kept",
    dependencies=[Can(Permission.edit_content, at="task_id")],
)
async def drop_task_bundle(
    project_id: uuid.UUID,
    task_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    credential: PresentedCredential,
) -> dict:
    """The task's checkout has nothing uncommitted: its snapshot goes."""
    task = await _task_for(db, project_id, task_id, credential)
    await kept_work.drop_bundle(db, task.id)
    return ok({})
