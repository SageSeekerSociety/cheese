"""Where each agent works, read for the pages that show it: a conversation's
agents with their environments, a project's spread over environments, and the
sessions on one person's device.

Read-only. What changes an agent's environment is
``session_work.switch_environment``.
"""

from sqlalchemy import select

from app.domain.agent.environment import SelfHosted, spec_of, stored
from app.domain.agent.models import AgentTurn
from app.domain.agent_session.models import AgentEnvironment, AgentSession
from app.domain.agent_session.services import AgentSessionService
from app.domain.conversation.services import of_room, of_rooms, room_column
from app.domain.device.wiring import sql_device_service
from app.domain.machine.session_work import _agent_name, _session_author, presentation
from app.domain.project.services import ProjectService
from app.domain.room_task.models import Task
from app.domain.topic.models import Topic, TopicStatus


async def conversation_sessions(db, project, place) -> list[dict]:
    """Each agent with a session in this conversation (a room's own, or a
    task's), with the seat it acts under, its environment (None until it first
    needs one) and the machine it holds. One entry per agent, its most recent
    session: a change of harness opens another session for the same agent."""
    rows = await db.scalars(
        select(AgentSession)
        .where(AgentSession.conversation_id == place.conversation_id)
        .order_by(AgentSession.agent_handle, AgentSession.updated_at.desc())
    )
    chosen = await AgentSessionService(db).environments_in(place.conversation_id)
    out, seen = [], set()
    for row in rows:
        if row.agent_handle in seen:
            continue
        seen.add(row.agent_handle)
        shown = presentation(row)
        lease = shown["lease"] or {}
        environment = chosen.get(row.agent_handle)
        out.append(
            {
                "session_id": shown["id"],
                "agent_handle": row.agent_handle,
                "seat_handle": await _session_author(db, project, row.agent_handle),
                "environment": environment.environment if environment else None,
                "status": lease.get("status"),
                "online": bool(lease.get("online")),
            }
        )
    return out


async def _placed_sessions(db, project_id):
    """Each agent in the project's open rooms that has an environment, with
    its room, its session and that environment."""
    rows = await db.execute(
        select(AgentEnvironment, AgentSession, Topic)
        .select_from(AgentEnvironment)
        .join(
            AgentSession,
            (AgentSession.conversation_id == AgentEnvironment.conversation_id)
            & (AgentSession.agent_handle == AgentEnvironment.agent_handle),
        )
        .join(Topic, Topic.id == room_column(AgentSession.conversation_id))
        .where(Topic.project_id == project_id, Topic.status != TopicStatus.archived)
        .order_by(AgentSession.updated_at.desc())
    )
    placed, seen = [], set()
    for chosen, row, topic in rows:
        key = (row.conversation_id, row.agent_handle)
        if key in seen:
            continue
        seen.add(key)
        environment = stored(chosen.environment)
        if environment is not None:
            placed.append((row, topic, environment))
    return placed


async def project_distribution(db, project_id) -> dict:
    """Where the project's agents that have an environment are: how many in
    sandboxes of each spec, and how many on each person's machine they run on
    unisolated."""
    specs: dict[str, int] = {}
    on_devices: dict[str, int] = {}
    for _row, _topic, environment in await _placed_sessions(db, project_id):
        if isinstance(environment, SelfHosted):
            on_devices[environment.device_id] = (
                on_devices.get(environment.device_id, 0) + 1
            )
        else:
            specs[environment.spec] = specs.get(environment.spec, 0) + 1
    devices = (
        await sql_device_service(db).get_devices(list(on_devices)) if on_devices else {}
    )
    listed = [
        {
            "device_id": device_id,
            "name": devices[device_id].name if device_id in devices else None,
            "agents": agents,
        }
        for device_id, agents in on_devices.items()
    ]
    listed.sort(key=lambda entry: (-entry["agents"], entry["name"] or ""))
    return {
        "sandboxes": [
            {"spec": spec, "agents": agents}
            for spec, agents in sorted(specs.items(), key=lambda kv: -kv[1])
            if spec_of(spec) is not None
        ],
        "devices": listed,
    }


async def device_sessions(db, project_id, device_id: str) -> list[tuple]:
    """The project's agents on one person's device — run there unisolated, or
    in a sandbox it holds — as ``(room, session presentation)`` pairs, most
    recently active first.

    ``working`` is whether the room has a turn running. Turns are recorded per
    room, not per session, so a session whose room is mid-turn counts as
    working: a bulk switch skips it rather than take its machine away mid-turn.
    """

    placed = [
        (row, topic, environment)
        for row, topic, environment in await _placed_sessions(db, project_id)
        if (row.work_lease or {}).get("device_id") == device_id
        or (isinstance(environment, SelfHosted) and environment.device_id == device_id)
    ]
    if not placed:
        return []
    project = await ProjectService(db).get_or_404(project_id)
    busy = set(
        await db.scalars(
            select(room_column(AgentTurn.conversation_id)).where(
                of_rooms(
                    AgentTurn.conversation_id, {topic.id for _row, topic, _e in placed}
                ),
                AgentTurn.stopped_at.is_(None),
            )
        )
    )

    task_ids = {
        row.conversation_id
        for row, topic, _e in placed
        if row.conversation_id != topic.id
    }
    task_titles = (
        dict(
            (
                await db.execute(
                    select(Task.id, Task.title).where(Task.id.in_(task_ids))
                )
            ).all()
        )
        if task_ids
        else {}
    )
    agent_names: dict[tuple, dict] = {}

    out = []
    for row, topic, environment in sorted(
        placed, key=lambda entry: entry[0].updated_at, reverse=True
    ):
        has_task = row.conversation_id in task_titles
        agent_key = (topic.id, row.agent_handle)
        if agent_key not in agent_names:
            agent_names[agent_key] = await _agent_name(
                db, project, topic, row.agent_handle
            )
        out.append(
            (
                topic,
                {
                    "id": str(row.id),
                    "topic_id": str(topic.id),
                    "task_id": str(row.conversation_id) if has_task else None,
                    "task_title": task_titles.get(row.conversation_id),
                    "topic_title": topic.title,
                    "agent_handle": row.agent_handle,
                    **agent_names[agent_key],
                    "environment": environment.model_dump(),
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


async def leases_on_peoples_devices(db, room_id) -> list[dict]:
    """The leases the room's sessions (its tasks' included) hold on people's
    own computers, most recently placed first. A sandbox on the platform's
    machines is the platform's to report (``CloudHostRepository.room_homes``)."""
    leases = await db.scalars(
        select(AgentSession.work_lease)
        .where(
            of_room(AgentSession.conversation_id, room_id),
            AgentSession.work_lease.is_not(None),
        )
        .order_by(AgentSession.placed_at.desc().nulls_last())
    )
    return [
        lease
        for lease in leases
        if lease and lease.get("device_id") and not lease.get("platform")
    ]
