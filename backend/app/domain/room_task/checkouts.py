"""Removing closed tasks' checkouts from the machines a room works on.

A room opens a checkout per task under its home on a machine
(`~/.cheese/tasks/<task>`). Nothing else removes it before the room is
archived, so an old room carries every task it ever had — 56 checkouts, about
20 GB, in one room on dev.

The platform knows which tasks are closed; only the machine can tell whether a
checkout still holds something its forge does not. So the platform names the
room's closed tasks and `resource_cleanup.remove_task_checkouts` decides per
checkout, keeping any with uncommitted files, unpushed commits or a process
inside. It goes over the device exec the room's own archive cleanup uses,
which needs no executor running there: an idle session usually has none.

Two moments start it. A task closing, for every machine the room holds a
lease on — the current ones and those it has left (`retained_leases`), whose
checkouts stay behind when a room moves. And a machine connecting, for every
lease on it: a machine that was offline when its task closed is swept the
moment it is back, including one the room has left and will never start an
executor on again. Each run names all of the room's closed tasks, so a
checkout kept once is tried again on the next run.
"""

import json
import logging
import uuid
from pathlib import Path

from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

logger = logging.getLogger("cheesex.room_task.checkouts")

# Removing checkouts of a few GB each, after one `lsof` over all of them.
EXEC_TIMEOUT_S = 300.0
# How much of one command line the task ids may take. Windows refuses a
# command line past 32,767 characters ("The filename or extension is too
# long"), and a channel that has closed a thousand tasks names more than that;
# so the ids go in batches well under it, leaving room for the interpreter path.
ARGV_BUDGET = 8000


async def remove_closed_checkouts(
    sessions, *, room_id: uuid.UUID | None = None, device_id: str | None = None
) -> dict[str, int]:
    """Ask each machine holding a lease of ``room_id`` (or every lease on
    ``device_id``) to remove its closed tasks' checkouts; answer how many
    went and how many were kept."""
    from app.domain.agent import resource_cleanup
    from app.domain.agent.device_hub import device_hub
    from app.domain.agent_session.models import AgentSession
    from app.domain.conversation.services import of_room, room_column
    from app.domain.room_task.models import Task, TaskStatus
    from app.domain.topic.models import Topic, TopicStatus

    # (device, work resource) -> (project, room). An archived room's own
    # cleanup removes its whole home; this leaves it to that.
    places: dict[tuple[str, str], tuple[uuid.UUID, uuid.UUID]] = {}
    closed: dict[uuid.UUID, list[str]] = {}
    async with sessions() as db:
        query = (
            select(AgentSession, Topic)
            .select_from(AgentSession)
            .join(Topic, Topic.id == room_column(AgentSession.conversation_id))
            .where(Topic.status != TopicStatus.archived)
        )
        if room_id is not None:
            query = query.where(of_room(AgentSession.conversation_id, room_id))
        for conversation, topic in (await db.execute(query)).all():
            for lease in [
                *(conversation.execution_request or {}).get("retained_leases", []),
                *([conversation.work_lease] if conversation.work_lease else []),
            ]:
                device, resource = lease.get("device_id"), lease.get("resource_id")
                if not device or not resource:
                    continue
                if device_id is not None and device != device_id:
                    continue
                places[(device, resource)] = (topic.project_id, topic.id)
        rooms = {room for _project, room in places.values()}
        if rooms:
            rows = await db.execute(
                select(Task.room_id, Task.id).where(
                    Task.room_id.in_(rooms), Task.status == TaskStatus.closed
                )
            )
            for room, task in rows.all():
                closed.setdefault(room, []).append(str(task))
    counts = {"removed": 0, "kept": 0}
    script = Path(resource_cleanup.__file__).read_text()
    for (device, resource), (project, room) in places.items():
        tasks = closed.get(room)
        if not tasks or not device_hub.is_online(device):
            continue
        outcome: dict = {"removed": [], "kept": {}}
        try:
            for batch in _batches(tasks):
                result = await device_hub.exec(
                    device,
                    ["python3", "-", "tasks", str(project), resource, "-", "-", *batch],
                    stdin=script,
                    timeout=EXEC_TIMEOUT_S,
                )
                if result.get("exit") != 0 or result.get("truncated"):
                    raise RuntimeError(
                        str(result.get("stderr") or "no answer from the machine")[
                            -1500:
                        ]
                    )
                answer = json.loads(result["stdout"])
                outcome["removed"].extend(answer["removed"])
                outcome["kept"].update(answer["kept"])
        except Exception:  # noqa: BLE001 — one machine must not stop the others
            # WARNING, not ERROR: a machine going away mid-run is the usual
            # cause, and its next connection runs this again.
            logger.warning(
                "closed task checkouts not removed device=%s room=%s",
                device,
                room,
                exc_info=True,
            )
            continue
        counts["removed"] += len(outcome["removed"])
        counts["kept"] += len(outcome["kept"])
        for task, reason in outcome["kept"].items():
            logger.info(
                "closed task checkout kept device=%s room=%s task=%s: %s",
                device,
                room,
                task,
                reason,
            )
    return counts


def _batches(tasks: list[str]) -> list[list[str]]:
    """The task ids in runs whose command line stays within ``ARGV_BUDGET``."""
    batches: list[list[str]] = [[]]
    used = 0
    for task in tasks:
        if batches[-1] and used + len(task) + 1 > ARGV_BUDGET:
            batches.append([])
            used = 0
        batches[-1].append(task)
        used += len(task) + 1
    return batches


def after_close(db: AsyncSession, room_id: uuid.UUID) -> None:
    """Once ``db`` commits the close of a task in ``room_id``, remove the
    room's closed checkouts. Every place that closes a task calls this; a
    close that is rolled back runs it at the next commit instead, which
    removes nothing that is not closed."""
    from app.core.background import spawn

    pending = db.info.setdefault("closed_task_rooms", set())
    if room_id in pending:
        return
    pending.add(room_id)
    # The committing session's own database, as a request's or a test's.
    factory = async_sessionmaker(db.bind, expire_on_commit=False)

    def committed(_session) -> None:
        pending.discard(room_id)
        spawn(
            remove_closed_checkouts(factory, room_id=room_id),
            name="closed task checkouts",
        )

    event.listen(db.sync_session, "after_commit", committed, once=True)
