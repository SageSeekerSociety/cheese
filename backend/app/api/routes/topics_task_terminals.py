"""A person's terminals in a task: which are there, and closing one.

The shells are the machine's (`machine.terminals`): a page asks which of the
person's are there when it opens the task, and shows a 终端 tab for each, so
a page closed and opened again finds the same ones. Watching and typing into
one goes over its own socket, served by the device connection's owner
(`routes/connector/terminal.py`).

- ``GET /topics/{task}/terminals`` lists the caller's shells there, or says
  why the caller may not open one (``refused``).
- ``DELETE /topics/{task}/terminals/{id}`` ends one of them.
"""

import logging
import uuid
from typing import Annotated, Literal

from fastapi import APIRouter
from pydantic import BaseModel

from app.api.request_deps import DbSession
from app.api.response import ok, typed_response
from app.api.write_access import Can, InChannel
from app.core.errors import ForbiddenError, NotFoundError, ValidationError
from app.core.sentences import say
from app.domain.agent.device_hub import DeviceCallError, DeviceOffline, device_hub
from app.domain.machine import preview_wake, terminals
from app.domain.project_permissions import Permission
from app.domain.room_task.place import TaskStatus
from app.domain.user.services import user_by_handle

router = APIRouter(prefix="/topics", tags=["topics"])
logger = logging.getLogger(__name__)

#: Whoever may edit the task's files may open a terminal in it.
Edits = Annotated[InChannel, Can(Permission.edit_content, at="topic_id")]

#: How long a listing waits for the machine.
LIST_TIMEOUT_S = 20


class TaskTerminalOut(BaseModel):
    id: str
    #: The shell exited; it stays listed until it is closed.
    ended: bool


class TaskTerminalsOut(BaseModel):
    terminals: list[TaskTerminalOut]
    #: Why the caller may not open a terminal here: ``not_owner`` (the task
    #: works on someone else's own computer), ``unsupported`` (its machine
    #: cannot hold one), ``outdated`` (its machine needs an update first).
    refused: Literal["not_owner", "unsupported", "outdated"] | None


class TaskTerminalClosedOut(BaseModel):
    closed: bool


async def _person(db, at: InChannel) -> tuple[uuid.UUID, int, str, bool]:
    """The open task, and the person asking: their id, their handle, and
    whether the task works in the room's workspace (it has no checkout of
    its own)."""
    task = at.place.task
    if task is None:
        raise NotFoundError(say("taskNotFound"))
    if task.status != TaskStatus.open:
        raise ValidationError(say("taskEndedFilesReadOnly"))
    if at.actor.via == "cheese":
        # A terminal is a person's: an AI teammate runs its own commands.
        raise ForbiddenError(say("projectPermissionDenied"))
    user_id = at.actor.user_id
    if user_id is None:
        user = await user_by_handle(db, at.actor.handle)
        if user is None:
            raise ForbiddenError(say("projectPermissionDenied"))
        user_id = user.id
    return task.id, user_id, at.actor.handle, not task.branch_name


@router.get("/{topic_id}/terminals", **typed_response(TaskTerminalsOut))
async def task_terminals(topic_id: uuid.UUID, at: Edits, db: DbSession) -> dict:
    """The caller's shells in the task's environment, oldest first. Nothing
    is listed while no machine holds the task's copy with the caller attached
    to it: no shell of theirs can be there."""
    task_id, user_id, handle, in_workspace = await _person(db, at)
    refused = await terminals.refusal(db, task_id, user_id, hub=device_hub)
    if refused is not None:
        return ok({"terminals": [], "refused": refused})
    shells = await terminals.shells_of(
        db, task_id, user_id=user_id, handle=handle, in_workspace=in_workspace
    )
    await db.commit()
    if shells is None:
        return ok({"terminals": [], "refused": None})
    # A frozen sandbox answers nothing until it is woken.
    await preview_wake.wake_for_files(db.bind, task_id)
    try:
        found = await device_hub.call_executor(
            shells.device_id,
            shells.state,
            "terminal_list",
            {"owner": shells.params["owner"]},
            timeout=LIST_TIMEOUT_S,
        )
    except DeviceCallError as exc:
        if terminals.UNKNOWN_METHOD in str(exc):
            return ok({"terminals": [], "refused": terminals.Refusal.outdated})
        logger.warning("listing terminals of task %s failed: %s", task_id, exc)
        return ok({"terminals": [], "refused": None})
    except (DeviceOffline, TimeoutError):
        return ok({"terminals": [], "refused": None})
    return ok({"terminals": found.get("terminals", []), "refused": None})


@router.delete(
    "/{topic_id}/terminals/{terminal_id}", **typed_response(TaskTerminalClosedOut)
)
async def close_task_terminal(
    topic_id: uuid.UUID, terminal_id: str, at: Edits, db: DbSession
) -> dict:
    """End one of the caller's shells in the task, with everything it
    runs. One that is not there is closed already."""
    task_id, user_id, handle, in_workspace = await _person(db, at)
    shells = await terminals.shells_of(
        db, task_id, user_id=user_id, handle=handle, in_workspace=in_workspace
    )
    await db.commit()
    if shells is None:
        return ok({"closed": False})
    try:
        closed = await device_hub.call_executor(
            shells.device_id,
            shells.state,
            "terminal_close",
            {"owner": shells.params["owner"], "id": terminal_id},
            timeout=LIST_TIMEOUT_S,
        )
    except (DeviceCallError, DeviceOffline, TimeoutError) as exc:
        logger.warning("closing a terminal of task %s failed: %s", task_id, exc)
        return ok({"closed": False})
    return ok({"closed": bool(closed.get("closed"))})
