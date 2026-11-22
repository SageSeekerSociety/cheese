"""A person brings a task's machine back before editing its files.

The task's files open from the forge while no machine holds them; editing
them needs one. The page asks for it ahead of the first save — on the first
keystroke, or when a terminal opens — and watches it come up, the way an app
preview's wake is asked for and watched (`topics_preview_app`):

- ``POST /topics/{task}/machine/prepare`` starts bringing it back, in the
  caller's name, and answers at once.
- ``GET /topics/{task}/machine/status`` is what the page polls meanwhile.

A save does the same on its own when nothing was asked first
(`workspace.write_file`).
"""

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter
from pydantic import BaseModel

from app.api.request_deps import DbSession
from app.api.response import ok, typed_response
from app.api.write_access import Can, InChannel
from app.core.errors import NotFoundError, ValidationError
from app.core.sentences import say
from app.domain.machine import task_machine
from app.domain.project_permissions import Permission
from app.domain.room_task.place import TaskStatus

router = APIRouter(prefix="/topics", tags=["topics"])

#: Whoever may save the task's files may bring its machine back for them.
Edits = Annotated[InChannel, Can(Permission.edit_content, at="topic_id")]


class MachinePrepareOut(BaseModel):
    started: bool
    #: Why nothing was started: ``preparing`` (it is being brought back
    #: already).
    reason: Literal["preparing"] | None = None


class MachineStatusOut(BaseModel):
    #: ``ready``: a machine holds the task's copy; ``preparing``: one is being
    #: brought back; None: none holds it.
    machine: Literal["ready", "preparing"] | None
    #: Why the last bring-up did not finish: ``unavailable`` (no machine could
    #: be had for it). None when it did, or none was asked.
    failed: Literal["unavailable"] | None


def _task_of(at: InChannel) -> uuid.UUID:
    task = at.place.task
    if task is None:
        raise NotFoundError(say("taskNotFound"))
    if task.status != TaskStatus.open:
        raise ValidationError(say("taskEndedFilesReadOnly"))
    return task.id


@router.post(
    "/{topic_id}/machine/prepare",
    **typed_response(MachinePrepareOut),
)
async def prepare_task_machine(topic_id: uuid.UUID, at: Edits, db: DbSession) -> dict:
    """Bring the task's machine back for the caller, with no turn: its
    sandbox, the task's checkout on it and the work it kept laid back
    (`machine.task_machine`). Answers at once; ``status`` says how far it got.
    A task nobody has worked in yet gets its default machine."""
    # A machine the record says is ready is checked again end to end too: its
    # executor may have stopped since (`task_machine._bring_up`).
    started = await task_machine.prepare(db, _task_of(at), at.actor)
    return ok({"started": started, "reason": None if started else "preparing"})


@router.get(
    "/{topic_id}/machine/status",
    **typed_response(MachineStatusOut),
)
async def task_machine_status(topic_id: uuid.UUID, at: Edits, db: DbSession) -> dict:
    """Where bringing the task's machine back has got to."""
    if at.place.task is None:
        raise NotFoundError(say("taskNotFound"))
    now = await task_machine.status(db, at.place.task.id)
    return ok({"machine": now.machine, "failed": now.failed})
