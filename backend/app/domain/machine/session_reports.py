"""Where each agent session works, read for the pages that show it: a room's
or a task's sessions with their machines, a project's spread over machines,
and the sessions on one device.

Read-only. What changes a session's machine is ``session_work``.
"""

from sqlalchemy import select

from app.domain.agent.device_hub import device_hub
from app.domain.agent_session.models import AgentSession
from app.domain.device.supply import Visibility
from app.domain.device.wiring import sql_device_service
from app.domain.machine.session_work import _agent_name, _visibility_of
from app.domain.project.services import ProjectService


def presentation(row):
    request = row.execution_request or {}
    lease = row.work_lease
    shown = None
    if lease:
        online = device_hub.is_online(lease["device_id"])
        # A cloud session's sandbox is on one of the platform's hosts. Which one,
        # and where on it, is the platform's scheduling and nobody's to read.
        on_cloud = (request.get("choice") or {}).get("profile") == "cloud"
        shown = (
            {"status": lease.get("status", "ready"), "online": online}
            if on_cloud
            else {**lease, "online": online}
        )
    return {
        "id": str(row.id),
        "agent_handle": row.agent_handle,
        "harness": row.harness,
        "choice": request.get("choice"),
        "lease": shown,
    }


async def session_machines(db, topic, task_id=None) -> list[dict]:
    """Each agent session of the room's own conversation (or of one of its
    tasks), with what it works on.

    A choice is every one of its sessions' choice. On a self-hosted device each
    row answers that **one** machine — the lease's when the session holds one,
    else the one the choice will lease; on the cloud each session has its own
    sandbox, on a platform host it does not name. ``choice`` is None only for a
    session that has not started working.
    """
    devices = sql_device_service(db)
    rows = await db.scalars(
        select(AgentSession)
        .where(
            AgentSession.topic_id == topic.id,
            AgentSession.conversation_id == (task_id or topic.id),
        )
        .order_by(AgentSession.agent_handle, AgentSession.created_at)
    )
    out = []
    for row in rows:
        choice = (row.execution_request or {}).get("choice")
        visibility = None
        if row.work_lease:
            visibility = await _visibility_of(
                db, devices, topic.id, row.work_lease.get("device_id")
            )
        elif choice and choice.get("profile") == "device":
            visibility = await _visibility_of(
                db, devices, topic.id, choice.get("device_id")
            )
        out.append(
            {
                **presentation(row),
                "machine_access": visibility is Visibility.host,
                "visibility": visibility,
            }
        )
    return out


async def _placed_sessions(db, project_id):
    """Each agent session in the project's open rooms that has a machine, with
    its room and the device it is on (None for Cloud, and for a device the
    platform picks when the session leases).

    A session that has not started working has no machine and is left out:
    the project default decides where it goes.
    """
    from app.domain.topic.models import Topic, TopicStatus

    rows = await db.execute(
        select(AgentSession, Topic)
        .join(Topic, Topic.id == AgentSession.topic_id)
        .where(Topic.project_id == project_id, Topic.status != TopicStatus.archived)
    )
    placed = []
    for row, topic in rows:
        choice = (row.execution_request or {}).get("choice")
        if not choice:
            continue
        device_id = None
        if choice.get("profile") != "cloud":
            device_id = (row.work_lease or {}).get("device_id") or choice.get(
                "device_id"
            )
        placed.append((row, topic, choice, device_id))
    return placed


async def project_distribution(db, project_id) -> dict:
    """Where the project's agents that have started work are, right now.

    Counted per agent session in the project's open rooms: how many are in
    cloud sandboxes, how many on whole cloud VMs, and how many on each
    self-hosted device, with whether an agent there can see the whole machine.
    """
    cloud = cloud_vm = 0
    on_devices: dict[str | None, dict] = {}
    devices = sql_device_service(db)
    for _row, topic, choice, device_id in await _placed_sessions(db, project_id):
        if choice.get("profile") == "cloud":
            if choice.get("whole_machine"):
                cloud_vm += 1
            else:
                cloud += 1
            continue
        entry = on_devices.setdefault(
            device_id,
            # Only a device's own name is shown; a label stored on "any online
            # device" is one language's words and is not handed out.
            {
                "device_id": device_id,
                "name": choice.get("name") if device_id else None,
                "agents": 0,
                "machine_access": False,
            },
        )
        entry["agents"] += 1
        # The machine is open to its agents when any room there was given it
        # whole: access is chosen per room, not per machine.
        visibility = await _visibility_of(db, devices, topic.id, device_id)
        entry["machine_access"] |= visibility is Visibility.host
    listed = []
    for entry in on_devices.values():
        if entry["device_id"] is not None:
            device = await devices.get_device(entry["device_id"])
            if device is not None:
                entry["name"] = device.name
        listed.append(entry)
    listed.sort(key=lambda entry: (-entry["agents"], entry["name"] or ""))
    return {"cloud": cloud, "cloud_vm": cloud_vm, "devices": listed}


async def device_sessions(db, project_id, device_id: str) -> list[tuple]:
    """The project's agent sessions on one device, the ones its distribution
    counts there, as ``(room, session presentation)`` pairs, most recently
    active first.

    ``working`` is whether the room has a turn running. Turns are recorded per
    room, not per session, so a session whose room is mid-turn counts as
    working: a bulk switch skips it rather than take its machine away mid-turn.
    """
    from app.domain.agent.models import AgentTurn

    placed = [
        (row, topic)
        for row, topic, _choice, on in await _placed_sessions(db, project_id)
        if on == device_id
    ]
    if not placed:
        return []
    project = await ProjectService(db).get_or_404(project_id)
    busy = set(
        await db.scalars(
            select(AgentTurn.topic_id).where(
                AgentTurn.topic_id.in_({topic.id for _row, topic in placed}),
                AgentTurn.stopped_at.is_(None),
            )
        )
    )
    from app.domain.room_task.models import Task

    out = []
    for row, topic in sorted(placed, key=lambda pair: pair[0].updated_at, reverse=True):
        # A task's session is moved by changing the task's work computer.
        task = (
            await db.get(Task, row.conversation_id)
            if row.conversation_id != topic.id
            else None
        )
        out.append(
            (
                topic,
                {
                    "id": str(row.id),
                    "topic_id": str(topic.id),
                    "task_id": str(task.id) if task is not None else None,
                    "task_title": task.title if task is not None else None,
                    "topic_title": topic.title,
                    "topic_title_source": str(topic.title_source),
                    "agent_handle": row.agent_handle,
                    **await _agent_name(db, project, topic, row.agent_handle),
                    "choice": (row.execution_request or {}).get("choice"),
                    "last_active": row.updated_at.isoformat(),
                    "working": topic.id in busy,
                },
            )
        )
    return out


async def devices_held_by(db, conversation_id) -> set[str]:
    """The devices this conversation's sessions hold a lease on now."""
    leases = await db.scalars(
        select(AgentSession.work_lease).where(
            AgentSession.conversation_id == conversation_id,
            AgentSession.work_lease.is_not(None),
        )
    )
    return {lease["device_id"] for lease in leases if (lease or {}).get("device_id")}
