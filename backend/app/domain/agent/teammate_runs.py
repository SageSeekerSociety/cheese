"""Which runs its own teammate's sessions are in for each task, as their runners name them.

A task reads 在做 while its teammate's session runs, unless the teammate
reported in that same run (`room_task.presentation.task_presentation`). Two
facts go into that, and both are about the task's own teammate: a reviewer or
any other agent working in the task's conversation is not the task being done.
The teammate is the seat the task names (`Task.agent_handle`), or — when it
names none, or that seat has left the project — its room's, then the
project's default: the same answer `AgentInstanceService.for_tasks` gives
every other reader. Each running session's run is the one its runner named
when it began (`session_state.run_started`), read back by
`session_state.running_runs`.
"""

import uuid
from collections.abc import Iterable
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped

from app.domain.agent.session_state import running_conversations, running_runs
from app.domain.agent_instance.services import AgentInstanceService
from app.domain.identity.handles import agent_instance_handle
from app.domain.room_task.presentation import Run


class TaskPlace(Protocol):
    """The four fields that say which teammate a task is the work of, as a
    plain value carries them (`room_task.repositories.ListedTask`)."""

    @property
    def id(self) -> uuid.UUID: ...

    @property
    def project_id(self) -> uuid.UUID: ...

    @property
    def room_id(self) -> uuid.UUID: ...

    @property
    def agent_handle(self) -> str | None: ...


class TaskPlaceRow(Protocol):
    """The same four on the `Task` row, declared as the model maps them."""

    id: Mapped[uuid.UUID]
    project_id: Mapped[uuid.UUID]
    room_id: Mapped[uuid.UUID]
    agent_handle: Mapped[str | None]


async def teammate_runs(
    session: AsyncSession, tasks: Iterable[TaskPlace | TaskPlaceRow]
) -> dict[uuid.UUID, frozenset[Run]]:
    """{task: the runs its own teammate's running sessions are in} for the
    tasks whose teammate has a session running; the others are left out. A
    running session with no run recorded (its running event was applied before
    runs were recorded) adds none, so its task is in the answer with no run a
    report could match. Only the tasks with some session running are resolved
    to a teammate, so a page of quiet tasks costs one query."""
    by_id = {task.id: task for task in tasks}
    busy = await running_conversations(session, list(by_id))
    if not busy:
        return {}
    projects: dict[uuid.UUID, dict[uuid.UUID, tuple[uuid.UUID, str | None]]] = {}
    for task_id in busy:
        task = by_id[task_id]
        projects.setdefault(task.project_id, {})[task_id] = (
            task.room_id,
            task.agent_handle,
        )
    service = AgentInstanceService(session)
    seats: dict[uuid.UUID, str] = {}
    for project_id, of_project in projects.items():
        for task_id, teammate in (
            await service.for_tasks(project_id, of_project)
        ).items():
            seats[task_id] = agent_instance_handle(teammate.instance_id)
    runs = await running_runs(session, list(seats.items()))
    return {
        task_id: frozenset(
            Run(seat, harness, epoch, seq)
            for harness, epoch, seq in runs[(task_id, seat)]
        )
        for task_id, seat in seats.items()
        if (task_id, seat) in runs
    }
