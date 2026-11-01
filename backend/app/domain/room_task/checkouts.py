"""Removing closed tasks' checkouts from the machines a room works on.

A room opens a checkout per task under its home on a machine
(`~/.cheese/tasks/<task>`). Nothing else removes it before the room is
archived, so an old room carries every task it ever had — 56 checkouts, about
20 GB, in one room on dev.

The platform knows which tasks are closed, and names them; the machine removes
each checkout that no process is inside. A closed task's work is what it
pushed to its branch, so nothing waits on publication.
It goes over the device exec the room's own archive cleanup uses, which needs
no executor running there: an idle session usually has none.

Naming them is all this module does. Reaching the machine is a call this layer
cannot make, so it is declared in `ports.py` and handed in by whoever builds
us (`app.domain.agent.checkout_sweep` implements it).

Two moments start it. A task closing, for every machine the room holds a
lease on — the current ones and those it has left (`retained_leases`), whose
checkouts stay behind when a room moves. And a machine connecting, for every
lease on it: a machine that was offline when its task closed is swept the
moment it is back, including one the room has left and will never start an
executor on again. Each run names all of the room's closed tasks, so a
checkout something still had open is tried again on the next run.
"""

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core import background
from app.core.db import after_own_commit
from app.domain.agent_session.models import AgentSession
from app.domain.conversation.services import of_room, room_column
from app.domain.room_task.models import Task, TaskStatus
from app.domain.room_task.ports import SweepCheckouts
from app.domain.topic.models import Topic, TopicStatus

logger = logging.getLogger("cheesex.room_task.checkouts")


async def remove_closed_checkouts(
    sessions,
    *,
    room_id: uuid.UUID | None = None,
    device_id: str | None = None,
    machines: SweepCheckouts | None = None,
) -> dict[str, int]:
    """Ask each machine holding a lease of ``room_id`` (or every lease on
    ``device_id``) to remove its closed tasks' checkouts; answer how many
    went and how many were kept.

    ``machines`` is the call that reaches one machine with a list of task ids
    (`ports.SweepCheckouts`), handed in by whoever built us. None is the wiring
    missing, not a room without machines: a run that has checkouts to remove
    and no way to reach a machine raises, and an idle one answers zero.
    """
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
    for (device, resource), (project, room) in places.items():
        tasks = closed.get(room)
        if not tasks:
            continue
        if machines is None:  # pragma: no cover - 接线漏了才会走到
            # i18n-exempt: developer miswiring, not a refusal a reader can act on
            raise RuntimeError("checkout sweep was not handed in")
        try:
            outcome = await machines(device, str(project), resource, tasks)
        except Exception:  # one machine must not stop the others
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


def after_close(
    db: AsyncSession, room_id: uuid.UUID, *, machines: SweepCheckouts | None = None
) -> None:
    """Once ``db`` commits the close of a task in ``room_id``, remove the
    room's closed checkouts. Every place that closes a task calls this; a
    close that is rolled back runs it at the next commit instead, which
    removes nothing that is not closed."""
    pending = db.info.setdefault("closed_task_rooms", set())
    if room_id in pending:
        return
    pending.add(room_id)
    # The committing session's own database, as a request's or a test's.
    factory = async_sessionmaker(db.bind, expire_on_commit=False)

    def committed() -> None:
        pending.discard(room_id)
        background.spawn(
            remove_closed_checkouts(factory, room_id=room_id, machines=machines),
            name="closed task checkouts",
        )

    after_own_commit(db, committed)
