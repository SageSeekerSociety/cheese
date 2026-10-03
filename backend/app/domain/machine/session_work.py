"""Acquire one session's execution capability when a tool actually needs it."""

import asyncio
import json
import re
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from functools import partial

import httpx
from sqlalchemy import select

from app.core.config import settings
from app.core.errors import ConflictError, ForbiddenError, NotFoundError
from app.core.sandbox_auth import bind_resource_token
from app.core.sentences import say
from app.domain.agent import execution
from app.domain.agent.compute_configs import (
    ComputeChoice,
    choice_label,
    room_choice,
)
from app.domain.agent.device_hub import DeviceCallError, DeviceOffline, device_hub
from app.domain.agent.device_provider import (
    _preview_ws_url,
    device_api_base,
    device_home_dir,
    environment_status,
)
from app.domain.agent.harness.channel import mint_session_token
from app.domain.agent.harness.claude_code import executor_launch as launch
from app.domain.agent.market import COMPUTE_DEVICE, COMPUTE_TIERS
from app.domain.agent_session.models import AgentSession
from app.domain.agent_session.services import AgentSessionService
from app.domain.device.supply import (
    Supply,
    Visibility,
    binding_visibility,
    has_runnable_transport,
)
from app.domain.device.wiring import sql_device_service
from app.domain.identity.actor import Actor
from app.domain.machine.models import (
    GONE,
    MAX_ENROLL_ATTEMPTS,
    MachineStatus,
    ProjectMachine,
)
from app.domain.machine.services import (
    CloudKeepsFailing,
    MachineService,
    left_unpushed_on,
)
from app.domain.policy import gate
from app.domain.project.environment import EnvironmentConfig, pin_environment
from app.domain.project.services import ProjectService
from app.domain.topic.models import TopicKind
from app.domain.topic.services import TopicService
from app.domain.user.services import user_by_handle


def presentation(row):
    request = row.execution_request or {}
    return {
        "id": str(row.id),
        "agent_handle": row.agent_handle,
        "harness": row.harness,
        "choice": request.get("choice"),
        "lease": {
            **row.work_lease,
            "online": device_hub.is_online(row.work_lease["device_id"]),
        }
        if row.work_lease
        else None,
    }


async def _visibility_of(devices, device_id: str | None) -> Visibility | None:
    """What an agent on this machine can see of it: a whole self-hosted machine,
    or nothing worth a notice. ``None`` for a device id stands for an enrolled
    machine the platform picks when the session leases, which is still one.

    Only machines a person enrolled count. A Cloud box is the room's own and
    seeing all of it grants nothing more (``device.supply.binding_visibility``).
    """
    supply = Supply.self_hosted
    if device_id is not None:
        device = await devices.get_device(device_id)
        if device is None:
            return None
        supply = device.supply
    return binding_visibility(supply) if supply is Supply.self_hosted else None


async def session_machines(db, topic) -> list[dict]:
    """Each agent session in the room, with the machine it works on.

    一个话题一个容器（2026-09-28 决定，推翻结论 60）：一个房间里的所有会话都在房间
    那一项算出来的那台机器上，所以这里每一行答的都是那**一台**——会话手上有租约就
    是租约那台，还没开工就是房间那一项将租的那台。``choice`` 只有「这条会话还没
    开工、还没租到手」时才可能是 ``None``。
    """
    devices = sql_device_service(db)
    rows = await db.scalars(
        select(AgentSession)
        .where(AgentSession.topic_id == topic.id)
        .order_by(AgentSession.agent_handle, AgentSession.created_at)
    )
    out = []
    for row in rows:
        choice = (row.execution_request or {}).get("choice")
        visibility = None
        if row.work_lease:
            visibility = await _visibility_of(devices, row.work_lease.get("device_id"))
        elif choice and choice.get("profile") == "device":
            visibility = await _visibility_of(devices, choice.get("device_id"))
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

    Counted per agent session in the project's open rooms: how many are on
    cloud, and how many on each self-hosted device, with whether an agent there
    can see the whole machine.
    """
    cloud = 0
    on_devices: dict[str | None, dict] = {}
    for _row, _topic, choice, device_id in await _placed_sessions(db, project_id):
        if choice.get("profile") == "cloud":
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
            },
        )
        entry["agents"] += 1
    devices = sql_device_service(db)
    listed = []
    for entry in on_devices.values():
        if entry["device_id"] is not None:
            device = await devices.get_device(entry["device_id"])
            if device is not None:
                entry["name"] = device.name
        visibility = await _visibility_of(devices, entry["device_id"])
        listed.append({**entry, "machine_access": visibility is Visibility.host})
    listed.sort(key=lambda entry: (-entry["agents"], entry["name"] or ""))
    return {"cloud": cloud, "devices": listed}


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
    out = []
    for row, topic in sorted(placed, key=lambda pair: pair[0].updated_at, reverse=True):
        out.append(
            (
                topic,
                {
                    "id": str(row.id),
                    "topic_id": str(topic.id),
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


async def _roommates_device(db, topic, resource: str) -> str | None:
    """这一个房间这一代上，已经在用的那台机器——房间里别的会话的手。

    一个话题一个容器（2026-09-28 决定，推翻结论 60）：同一个房间里的会话落在同一台
    机器上。所以「自动选一台」的房间不是每条会话各挑一台在线的——那正是同一间房里
    两个队友会站在两台机器上的那条路。房间里已经有手在这台上，这就是这台。

    只认**这一代**：房间重开会换代（``resource_id``），上一代残留的租约不是本次的
    手，它的机器也不再是房间的机器。
    """
    leases = await db.scalars(
        select(AgentSession.work_lease)
        .where(
            AgentSession.topic_id == topic.id,
            AgentSession.work_lease.is_not(None),
        )
        .order_by(AgentSession.placed_at, AgentSession.id)
    )
    for lease in leases:
        if (
            lease.get("kind") == "device"
            and lease.get("status", "ready") == "ready"
            and lease.get("room_resource_id") == resource
            and lease.get("device_id")
        ):
            return lease["device_id"]
    return None


async def room_machine_visibility(db, topic, project_settings) -> Visibility | None:
    """How much of a self-hosted machine the agents in this room can see.

    #282 / #358 原则八: whole-machine access is never granted silently, so the
    room shows it whenever its agents have it. 一个话题一个容器（2026-09-28 决定，
    推翻结论 60）：一间房里所有会话看的是同一台机器，而那台机器由房间那一项算出
    来——包括还没开工的房间，那正是它开工时会拿到的那一台。
    """
    choice = room_choice(topic, project_settings)
    if choice.profile != COMPUTE_DEVICE:
        return None
    # 「系统挑一台」的房间：挑中的那台就是房间里的手已经站着的那台。
    device_id = choice.device_id or await _roommates_device(
        db, topic, str(topic.resource_id or topic.id)
    )
    return await _visibility_of(sql_device_service(db), device_id)


async def _session_teammate(db, project, handle: str):
    """The saved teammate whose session is keyed ``handle``, or None.

    A session is keyed by its teammate's own handle, not by the seat it acts
    under, so this asks the same lookup that turns a session back into its
    author when it runs (``for_handle``). None for a handle that names no
    teammate (a room-derived one)."""
    from app.domain.agent_instance.services import AgentInstanceService

    try:
        return await AgentInstanceService(db).for_handle(project, handle)
    except NotFoundError:
        return None


async def _agent_name(db, project, topic, handle: str) -> dict:
    """The name a room shows for the agent whose session is keyed ``handle``:
    its saved teammate's, else the one the room falls back to. Beside it,
    whether that is still the name it was born with, which a screen shows in
    its reader's language."""
    from app.domain.agent_instance.services import AgentInstanceService

    teammate = await _session_teammate(db, project, handle)
    agent = (
        await TopicService(db).resolve_agent(topic)
        if teammate is None
        else AgentInstanceService.resolved(teammate)
    )
    return {
        "agent_name": agent.display_name,
        "agent_name_source": agent.name_source.value,
    }


async def _session_author(db, project, handle: str) -> str:
    """The seat the session keyed ``handle`` acts under: its teammate's, or the
    room-derived handle itself, which is its own seat."""
    from app.domain.agent_instance.services import AgentInstanceService

    teammate = await _session_teammate(db, project, handle)
    if teammate is None:
        return handle
    return await AgentInstanceService(db).ensure_identity(teammate)


async def device_users(db, device_ids: list[str]) -> dict[str, list[dict]]:
    """Who works on each of these devices now: every agent session whose lease
    is there, in a room that is not archived, with its project, room and agent.
    """
    from app.domain.project.models import Project
    from app.domain.topic.models import Topic, TopicStatus

    out: dict[str, list[dict]] = {device_id: [] for device_id in device_ids}
    if not device_ids:
        return out
    on_device = AgentSession.work_lease["device_id"].as_string()
    rows = await db.execute(
        select(AgentSession, Topic, Project, on_device)
        .join(Topic, Topic.id == AgentSession.topic_id)
        .join(Project, Project.id == Topic.project_id)
        .where(on_device.in_(device_ids), Topic.status != TopicStatus.archived)
        .order_by(Project.name, Topic.title, AgentSession.agent_handle)
    )
    for session, topic, project, device_id in rows:
        out[device_id].append(
            {
                "project_id": str(project.id),
                "project_name": project.name,
                "topic_id": str(topic.id),
                "topic_title": topic.title,
                "topic_title_source": str(topic.title_source),
                "agent_handle": session.agent_handle,
                **await _agent_name(db, project, topic, session.agent_handle),
            }
        )
    return out


async def tell_device_owner(db, *, topic, row, device, lease) -> None:
    """Tell a device's owner that an agent session started working on it.

    Notice only, no approval (#1900 step 5). One per session start: the event
    is the lease's work resource, so a later tool on the same lease, or the
    same lease prepared again, is the same event and reaches nobody twice. An
    owner who is a person in that room sees it on the room's roster already
    and is not told.
    """
    from app.domain.delivery.addressing import Event, Hand, address
    from app.domain.delivery.ledger import DeliveryEvent, deliver, event_id_for
    from app.domain.notification.models import NotificationType
    from app.domain.team.models import Team
    from app.domain.topic_membership.services import TopicMemberService
    from app.domain.user.models import User

    owner = await db.get(User, device.owner_user_id)
    if owner is None:
        return
    if owner.username in await TopicMemberService(db).people_handles(topic.id):
        return
    project = await ProjectService(db).get_or_404(topic.project_id)
    team = await db.get(Team, project.team_id)
    named = await _agent_name(db, project, topic, row.agent_handle)
    agent = named["agent_name"]
    visibility = await _visibility_of(sql_device_service(db), device.device_id)
    access = visibility is Visibility.host
    await deliver(
        db,
        DeliveryEvent(
            id=event_id_for(
                NotificationType.DEVICE_IN_USE, f"{row.id}:{lease['resource_id']}"
            ),
            type=NotificationType.DEVICE_IN_USE,
            payload={
                "projectId": str(project.id),
                "projectName": project.name,
                # The team page lists who is on each of the owner's machines.
                "teamHandle": team.handle if team is not None else None,
                "topicId": str(topic.id),
                "topicTitle": topic.title,
                "topicTitleSource": str(topic.title_source),
                "agentHandle": row.agent_handle,
                "agentName": agent,
                "agentNameSource": named["agent_name_source"],
                "deviceId": device.device_id,
                "deviceName": device.name,
                "machineAccess": access,
                # The sentence an email carries (`notification.maintenance`).
                "content": f"{agent} 开始在「{device.name}」上工作："
                f"{project.name} · {topic.title}"
                + ("，能访问整台机器" if access else ""),
            },
            occurred_at=datetime.now(UTC),
        ),
        address(Event(machine_owner=owner.username), Hand.participant),
    )


class SessionWorking(ConflictError):
    """The session's room is mid-turn, and the switch was asked not to take a
    machine away from a turn (``if_idle``, the project's bulk switch)."""


class WorkComputerUnreachable(ConflictError):
    """The machine a session is leaving could not run the push before a switch.

    The one refusal a person may override (``abandon_unpushed``): the work on
    that machine stays there, and a switch made anyway leaves it behind."""


# How long a switch waits for the machine it leaves to push. The executor gives
# a command 120s and then reports it as still running (``runtime.bash``).
PUSH_WAIT_S = 150.0
PUSH_UNREACHABLE = say("switchOldComputerUnreachable")
WORKING = say("switchWhileWorking")


async def _room_is_working(db, topic_id) -> bool:
    from app.domain.agent.models import AgentTurn

    running = await db.scalar(
        select(AgentTurn.id)
        .where(AgentTurn.topic_id == topic_id, AgentTurn.stopped_at.is_(None))
        .limit(1)
    )
    return running is not None


async def push_before_switch(
    lease: dict, start: Callable[[], Awaitable[dict]], *, keeps_files: bool
) -> list[str]:
    """Push the session's work to its branches on the machine it is leaving.

    The same command a turn's Stop checkpoint runs there (``cheese sync
    --all``): every task checkout's unpushed commits go to its branch, and
    what is not committed is backed up as a snapshot ``cheese recover``
    restores; a checkout with neither is not touched, so the push costs what
    is unpushed rather than how many tasks the room has opened. Raises
    ``WorkComputerUnreachable`` when the command could not run at all, and a
    ``ConflictError`` with the machine's own words when it ran and failed.

    Except for closed tasks on a machine that ``keeps_files`` (a self-hosted
    one, left as it is): a closed task is never pushed, only backed up, and
    its checkout stays on that machine after the switch, so a backup it could
    not make loses nothing. Those come back as warnings, one line per task,
    and the switch goes on. A Cloud machine is deleted after the switch, so
    there they refuse it like any other failure.

    The session's executor runs only while the session is in use, so on a
    machine that is online an idle session usually has none. ``start`` brings
    it up the way a tool call would before the push, and only a machine that
    is away, or that cannot start it, is unreachable. One that is running an
    older release is started again too: its ``cheese`` is the one it was
    installed with, and a sync fixed since would still fail there the old way.
    """
    # A lease that never finished installing has no executor to run the push.
    if not lease.get("state") or not _reachable(device_hub, lease["device_id"]):
        raise WorkComputerUnreachable(PUSH_UNREACHABLE)
    try:
        try:
            running = await execution.call(lease, "ping", {}, hub=device_hub)
        except DeviceCallError:
            running = None
        if running is None or not launch.can_prepare(running):
            lease = {**lease, "state": (await start())["state"]}
        result = await execution.call(
            lease,
            "control",
            {"subtype": "checkpoint", "request_id": f"switch-{uuid.uuid4()}"},
            hub=device_hub,
            timeout=PUSH_WAIT_S,
        )
    # DeviceOffline and DeviceCallError are RuntimeErrors, as is a failed start.
    except (RuntimeError, TimeoutError) as exc:
        raise WorkComputerUnreachable(
            say("switchOldComputerUnreachableBecause", error=str(exc))
        ) from exc
    if "error" in result:
        raise ConflictError(say("switchPushFailed", error=result["error"]))
    output = result["value"]
    if output.get("backgroundTaskId"):
        raise ConflictError(say("switchPushTimedOut"))
    said = output.get("stdout") or ""
    if not said.startswith("Exit code "):
        return []
    printed = said.partition("\n")[2]
    failed = _failed_tasks(printed)
    if failed and keeps_files and all(closed for closed, _ in failed):
        return [say("workLeftClosedTask", line=line) for _, line in failed]
    detail = "\n".join(line for _, line in failed) or printed.strip()[-600:] or said
    raise ConflictError(say("switchPushFailed", error=detail))


# The line ``cheese sync --all`` ends each task it could not sync with.
_TASK_FAILED = re.compile(r"^\[cheese\] (已结束的)?任务 (\S+) 同步失败：(.*)$")


def _failed_tasks(printed: str) -> list[tuple[bool, str]]:
    """Each task the push could not sync — whether it is a closed one, and
    the first line said about why.

    ``cheese sync --all`` prints nothing for a task it synced, and for one it
    could not, what went wrong and then a line naming the task; that line's own
    reason is only an exit status when the failure was an API call's. So the
    first line printed since the previous task's is where this task's reason
    starts. Every task gets its line: a failure that hits them all (a refused
    credential) otherwise showed only the last one or two."""
    failed, first = [], None
    for line in printed.splitlines():
        ended = _TASK_FAILED.match(line)
        if ended is None:
            if first is None and line.strip():
                first = line.strip().removeprefix("[cheese] ")
            continue
        closed, task, reason = ended.groups()
        failed.append(
            (
                closed is not None,
                say("workTaskSyncFailed", task=task, reason=first or reason),
            )
        )
        first = None
    return failed


def _executor_env(env, *, api, token, project_id, topic_id, author, work_resource):
    """What a session's executor runs with: the caller's ``CHEESE_*``/``GIT_*``
    values, then the platform's own for this session."""
    return {
        **{
            key: value
            for key, value in env.items()
            if key.startswith(("CHEESE_", "GIT_"))
        },
        "CHEESE_API": api,
        "CHEESE_TOKEN": token,
        "CHEESE_PROJECT": str(project_id),
        "CHEESE_TOPIC": str(topic_id),
        "CHEESE_AUTHOR": author,
        "CHEESE_PREVIEW_URL": _preview_ws_url(api),
        "CHEESE_RESOURCE_ID": work_resource,
        "GIT_AUTHOR_NAME": author,
        "GIT_AUTHOR_EMAIL": f"{author}@agent.cheese.local",
    }


async def _start_executor(hub, lease, *, device_id, project_id, work_resource, setup):
    """Bring a session's executor up on ``device_id`` and answer what it
    reports: a running one on this release is prepared in place, anything else
    (none running, another release) is installed, which starts it."""
    if lease and lease.get("state"):
        try:
            running = await execution.call(lease, "ping", {}, hub=hub)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 500:
                raise
            running = {}
        except RuntimeError:
            # Installation resumes the same resource, never a tool call.
            running = {}
        if launch.can_prepare(running):
            return await execution.call(
                lease,
                "prepare",
                launch.payload_for(
                    project_id,
                    uuid.UUID(work_resource),
                    setup,
                    running.get("files"),
                ),
                hub=hub,
            )
    installed = await hub.exec(
        device_id,
        ["python3", "-"],
        stdin=launch.script(project_id, uuid.UUID(work_resource), setup),
        timeout=660,
    )
    if installed.get("exit") != 0 or installed.get("truncated"):
        raise RuntimeError(installed.get("stderr") or "Executor setup failed")
    return json.loads(installed["stdout"])


async def _restart_executor(db, row, lease):
    """How to start again the executor of the session ``row`` on the machine
    its ``lease`` is on, as a tool call there would: with the credential a
    session launches with, minted now, since the one it last ran with may have
    expired."""
    topic = await TopicService(db).get_or_404(row.topic_id)
    project = await ProjectService(db).get_or_404(topic.project_id)
    author = await _session_author(db, project, row.agent_handle)
    resource = lease.get("room_resource_id") or str(topic.resource_id or topic.id)
    token = bind_resource_token(
        mint_session_token(project.id, topic.id, author),
        resource,
        session_id=str(row.id),
        lease_generation=lease.get("generation"),
    )
    # A lease recorded before it carried its own resource is the generation's,
    # as `_attempt` reads it.
    work_resource = lease.get("resource_id") or (row.execution_request or {}).get(
        "generation"
    )
    api = await device_api_base(db, lease["device_id"], settings.connector_public_base)
    # The room's environment, as a turn hands it over: the executor records the
    # one it was installed with, and the next turn's start is refused while the
    # two differ.
    environment = (
        EnvironmentConfig().snapshot()
        if topic.kind == TopicKind.root
        else await pin_environment(db, project.id, topic.id)
    )
    return partial(
        _start_executor,
        device_hub,
        lease,
        device_id=lease["device_id"],
        project_id=project.id,
        work_resource=work_resource,
        setup=_executor_env(
            {"CHEESE_ENVIRONMENT": json.dumps(environment)},
            api=api,
            token=token,
            project_id=project.id,
            topic_id=topic.id,
            author=author,
            work_resource=work_resource,
        ),
    )


async def lock_room(db, topic_id):
    """Lock the room, then its machine slot — the order every path taking both
    keeps. A room change that took the slot first and then waited for the room
    deadlocked with an agent getting its machine, which holds the room and waits
    for the slot; Postgres killed one of the two and it answered 500."""
    from app.domain.machine.repositories import ProjectMachineRepository

    topic = await TopicService(db).lock_for_execution(topic_id)
    await ProjectMachineRepository(db).lock_topic(topic_id)
    return topic


async def request_choice(
    db,
    *,
    topic_id,
    actor,
    choice,
    abandon_unpushed=False,
    if_idle=False,
):
    """Point the whole room — and every session in it — at another work computer.

    一个话题一个容器（2026-09-28 决定，推翻结论 60）：一间房只有一条算力选择，房间里
    坐着的每一条会话都工作在那一项算出来的那台机器上，所以「换工作电脑」是房间的动
    作，不是某一条会话自己的。人打开选择器、或者房间里的某一轮拿着自己的凭据来改
    （``PUT /topics/{id}/compute-profile``），改的都是这一间房。

    每一条会话各自先在它离开的那台上把改动推上去（``_move_session``，逐条的推送与
    云机器的归还都还在那里）。**全部搬完才写房间那一项**：一条推不上去就是整个房间
    不换，而房间那一项没变时，已经搬动过的那几条会在下一轮自己走回来——``_attempt``
    从房间那一项解析，不是从会话行上那份副本。

    人的那一次换机可以不带推送（``abandon_unpushed``），且只在这一条会话原来那台
    够不着的时候成立；房间正在跑任务的房间，``if_idle`` 时整个不换
    （``SessionWorking``）。
    """
    # 「这个选择成不成立、可不可以自己发生、谁付钱」房间那条路由已经答过（池接没
    # 接入、设备归属、档位闸门、Cloud 的花钱权），这里只做搬：同一个选择原样落到每
    # 一条会话上。「系统挑一台」也原样落下去，由第一条要手的会话挑、其余的跟着它
    # （``_roommates_device``）。
    await TopicService(db).lock_for_execution(topic_id)
    sessions = await AgentSessionService(db).ids_in_room(topic_id)
    warnings: list[str] = []
    for session_id in sessions:
        warnings += await _move_session(
            db,
            topic_id=topic_id,
            session_id=session_id,
            actor=actor,
            choice=choice,
            abandon_unpushed=abandon_unpushed,
            if_idle=if_idle,
        )
    topic = await TopicService(db).lock_for_execution(topic_id)
    topic.compute_config = choice.model_dump()
    if warnings:
        await _tell_room_what_stayed_behind(db, topic_id, warnings)
    await db.commit()
    return {
        "choice": choice.model_dump(),
        "sessions": len(sessions),
        "warnings": warnings,
    }


async def _tell_room_what_stayed_behind(db, topic_id, warnings: list[str]) -> None:
    """The room moved, and closed tasks whose leftover work could not be
    backed up still have it only on the machine it left, which keeps it."""
    from app.domain.agent.announce import announce
    from app.domain.agent.platform_notices import (
        EVENT_WORK_LEFT_ON_MACHINE,
        SEVERITY_WARN,
        WHO_HUMAN,
        notice,
    )

    await announce(
        db,
        place_id=topic_id,
        content=say("workLeftOnMachine"),
        meta=notice(
            EVENT_WORK_LEFT_ON_MACHINE,
            severity=SEVERITY_WARN,
            who=WHO_HUMAN,
            detail=say("lines", items=warnings),
            detail_label=say("labelTasksNotBackedUp"),
        ),
    )


def _still_preparing(lease: dict | None) -> bool:
    """Whether a request is installing this lease right now.

    Only a live claim says so. A lease left ``preparing`` after its install
    failed or was abandoned, or waiting on a project environment, has nobody
    finishing it; treating it as busy refused every switch of its room for
    good, the way back to a machine that works included.
    """
    until = (lease or {}).get("claim_until")
    return bool(until) and datetime.fromisoformat(until) > datetime.now(UTC)


async def _move_session(
    db,
    *,
    topic_id,
    session_id,
    actor,
    choice,
    abandon_unpushed=False,
    if_idle=False,
):
    """Point one session at the room's work computer, after its work is pushed.

    The push runs on the machine the session leaves, with no transaction open.
    Only a person may switch without it, and only when that machine could not
    be reached (``abandon_unpushed``). A Cloud machine left after a push is
    deleted, so it stops counting against the team's quota. With ``if_idle``
    a session whose room is mid-turn is left alone (``SessionWorking``).
    Returns the push's warnings (``push_before_switch``).
    """
    # 房间那一把锁：这一条会话的租约和房间的算力选择在同一行上改，拿着它读、拿着
    # 它写，别的请求看到的是「搬之前」或者「搬之后」，没有中间态。
    await TopicService(db).lock_for_execution(topic_id)
    row = await AgentSessionService(db).by_id(session_id, lock=True)
    if row is None or row.topic_id != topic_id:
        raise NotFoundError("Session not found")
    request = row.execution_request or {}
    old = row.work_lease
    previous = request.get("choice")
    if previous and ComputeChoice.model_validate(previous).model_dump(
        exclude={"name"}
    ) == choice.model_dump(exclude={"name"}):
        if choice.profile == "cloud" and not request.get("authorized_by"):
            row.execution_request = {**request, "authorized_by": asdict(actor)}
            await db.commit()
        return []
    if _still_preparing(old):
        raise ConflictError(say("machineAllocationInProgress"))
    if if_idle and await _room_is_working(db, topic_id):
        raise SessionWorking(WORKING)
    if old and await sql_device_service(db).get_device(old["device_id"]) is None:
        # The machine was unbound: nothing can reach it to push, and nothing
        # left on it can be recovered or cleaned up, so leaving it takes no
        # one's consent and keeps nothing for the room's cleanup.
        old = None
    pushed = False
    warnings: list[str] = []
    if old:
        generation = request.get("generation")
        start = await _restart_executor(db, row, old)
        await db.commit()
        try:
            # A Cloud machine is deleted once left; any other keeps its files.
            leaving_cloud = (request.get("choice") or {}).get("profile") == "cloud"
            warnings = await push_before_switch(
                old, start, keeps_files=not leaving_cloud
            )
            pushed = True
        except WorkComputerUnreachable:
            if not (abandon_unpushed and actor.via == "token"):
                raise
        await TopicService(db).lock_for_execution(topic_id)
        row = await AgentSessionService(db).by_id(session_id, lock=True)
        if row is None:
            raise NotFoundError("Session not found")
        request = row.execution_request or {}
        old = row.work_lease
        if request.get("generation") != generation or not old:
            raise ConflictError(say("workComputerJustSwitched"))
        if _still_preparing(old):
            raise ConflictError(say("machineAllocationInProgress"))
    on_cloud = (request.get("choice") or {}).get("profile") == "cloud"
    left = None
    if on_cloud:
        # The room's machine: detached only when no other session is on it.
        left = await MachineService(db).supersede_session_machine(
            session_id, actor=actor
        )
    # Whatever was on a Cloud machine is on its branches now (or it never held
    # a lease), so the machine goes once the room's last session has left it.
    # Anything else stays for the room's cleanup.
    release = left if left is not None and (pushed or not old) else None
    kept = [] if old is None or (on_cloud and pushed) else [old]
    row.execution_request = {
        "generation": str(uuid.uuid4()),
        "choice": choice.model_dump(),
        "authorized_by": asdict(actor),
        "retained_leases": [*request.get("retained_leases", []), *kept],
    }
    row.work_lease = None
    await db.commit()
    if release is not None and not await left_unpushed_on(
        db, topic_id, release.device_id
    ):
        await MachineService(db).release_left_machine(release.id)
    return warnings


# The room names a machine whose owner has since unbound it. It cannot come back
# under that id (a re-bind enrols a new one), so the room needs another choice.
UNBOUND = "这个房间选的工作电脑已经解绑，需要重新选择工作电脑；对话和平台工具仍可用。"


# A tool that arrives while its session's machine is still being prepared
# waits for it, as a native session on that machine would simply run the
# command once it is up. One request waits at most this long and then answers
# that the machine is still preparing; the executor asks again until its own
# operation deadline. It is kept well inside the 300s the HTTP front gives any
# response.
PREPARING_WAIT_S = 50.0
# How often a waiting request looks at the signal it waits on. Each look is
# one row read and the hub's in-memory online set.
PREPARING_POLL_S = 1.0
# A project environment is looked at through the machine itself, which is a
# whole preparation attempt: that one is re-tried less often.
ENVIRONMENT_POLL_S = 5.0
# An environment in one of these states will not become ready by waiting.
ENVIRONMENT_SETTLED = {"failed", "stopped"}


@dataclass(frozen=True, slots=True)
class _Preparing:
    """Why this attempt could not hand out hands yet, and what to watch.

    ``progress`` answers, without side effects: a message when preparation
    has failed for good, True when another attempt may now succeed, and
    False while there is nothing new.
    """

    message: str
    progress: Callable[[], Awaitable[str | bool]]
    interval: float = PREPARING_POLL_S
    detail: dict = field(default_factory=dict)


def _reachable(hub, device_id: str) -> bool:
    """Whether a call to this machine is worth making: it is online, or its link
    dropped moments ago and the call will wait for it to come back
    (``device_hub.reconnecting``). One that does not come back in time fails
    that call as offline, which each caller already answers."""
    return hub.is_online(device_id) or hub.reconnecting(device_id)


async def ensure(
    db,
    *,
    topic_id,
    session_id,
    claims,
    token,
    env,
    wait_s,
    gone,
    hub=None,
):
    """Hands for this session, waiting up to ``wait_s`` while they are prepared.

    ``gone`` reports that the caller has left (its command was stopped or
    timed out), which ends the wait without taking anything further.
    """
    hub = hub or device_hub
    clock = asyncio.get_running_loop().time
    deadline = clock() + wait_s
    while True:
        outcome = await _attempt(
            db,
            topic_id=topic_id,
            session_id=session_id,
            claims=claims,
            token=token,
            env=env,
            hub=hub,
        )
        if not isinstance(outcome, _Preparing):
            return outcome
        waited = False
        while True:
            verdict = await outcome.progress()
            # Nothing is held while waiting: each look ends its transaction.
            await db.commit()
            if isinstance(verdict, str):
                return {"unavailable": verdict, **outcome.detail}
            if verdict and waited:
                break
            if clock() >= deadline or await gone():
                return {
                    "unavailable": outcome.message,
                    "preparing": True,
                    **outcome.detail,
                }
            await asyncio.sleep(max(0.0, min(outcome.interval, deadline - clock())))
            waited = True


async def _attempt(db, *, topic_id, session_id, claims, token, env, hub):
    """The row reservation survives worker death; remote work holds no DB lock."""
    topic = await TopicService(db).lock_for_execution(topic_id)
    sessions = AgentSessionService(db)
    row = await sessions.by_id(session_id, lock=True)
    if row is None or row.topic_id != topic_id:
        raise NotFoundError("Session not found")
    resource = str(topic.resource_id or topic.id)
    if (
        claims.get("session") != str(row.id)
        or claims.get("p") != str(topic.project_id)
        or claims.get("t") != str(topic_id)
        or claims.get("r") != resource
    ):
        raise ForbiddenError("Execution credential does not own this session")
    project = await ProjectService(db).get_or_404(topic.project_id)
    request = row.execution_request or {
        "generation": str(uuid.uuid4()),
        "authorized_by": None,
    }
    # 房间的选择就是这条会话的选择（2026-09-28 决定，推翻结论 60）。这一行上那份
    # ``choice`` 是**副本**，不是来源：手落在哪台机器上由房间那一项答，把它写回来是
    # 为了让读的人（名册、算力分布、清理清单）看到这一行与会话此刻真正在用的东西一
    # 致，而不是让解析去问它——两处各存一份、解析时听谁的那个问题，就是这条决定要
    # 消掉的东西。
    choice = room_choice(topic, project.settings)
    request = {**request, "choice": choice.model_dump()}
    row.execution_request = request
    generation = request["generation"]
    lease = row.work_lease
    if lease and lease.get("status", "ready") == "ready":
        # Recorded pre-existing resources retain their physical identity. A
        # new session never borrows another session's directory or lease.
        if not lease.get("generation"):
            lease = {**lease, "generation": generation, "session_id": str(row.id)}
            row.work_lease = lease
        if lease["generation"] != generation:
            raise ConflictError("The previous work lease has not been released")
    ready = bool(lease and lease.get("status", "ready") == "ready")
    if ready and (lease or {}).get("device_id") == (row.runtime_location or {}).get(
        "device_id"
    ):
        raise ForbiddenError("Project tools cannot execute on the session host")
    now = datetime.now(UTC)
    if lease and lease.get("claim_until"):
        if datetime.fromisoformat(lease["claim_until"]) > now:
            await db.commit()
            return _Preparing(
                "工作电脑正在准备；对话和平台工具仍可用。",
                partial(_claim_moved, db, session_id, lease.get("claim")),
            )
    devices = sql_device_service(db)
    selected = None
    if choice.profile == COMPUTE_DEVICE:
        if lease and await devices.get_device(lease["device_id"]) is None:
            # Its machine was unbound (a re-bind enrols a new device id): nothing
            # on it can be reached again, and returning to it would report the
            # room offline for good while its owner sees the machine online.
            row.work_lease = lease = None
        # 这条会话手上那台第一（租约是它现在真正在用的那台），房间点名的那台其次，
        # 再没有就问房间里的队友——同一个房间里的会话落在同一台机器上，所以「系统挑
        # 一台」不该由谁先来谁挑一台在线的来决定（``_roommates_device``）。
        device_id = (
            (lease or {}).get("device_id")
            or choice.device_id
            or await _roommates_device(db, topic, resource)
        )
        selected = await devices.get_device(device_id) if device_id else None
        if selected is None and device_id and device_id == choice.device_id:
            await db.commit()
            return {"unavailable": UNBOUND}
        if selected is None:
            selected = await devices.first_healthy_device(
                topic.project_id, hub.is_online
            )
        if selected is None or not _reachable(hub, selected.device_id):
            await db.commit()
            return {"unavailable": "工作电脑未连接；对话和平台工具仍可用。"}
        if selected.supply != Supply.self_hosted:
            raise ForbiddenError(
                "Choose self-hosted equipment or request a Cloud lease"
            )
        if not await devices.serves_project(selected.device_id, topic.project_id):
            raise ForbiddenError("Device no longer serves this project")
        if await devices.get_hosted_device(selected.device_id) is None:
            raise ForbiddenError("Device is not hosted")
        if not has_runnable_transport(
            await devices.binding_visibility(selected.device_id)
        ):
            raise ForbiddenError("Device has no supported execution isolation")
    approver = project.owner_handle or ""
    if selected is not None:
        from app.domain.user.models import User

        owner = await db.get(User, selected.owner_user_id)
        if owner is not None:
            approver = owner.username
    call = gate.Call(
        resource=gate.Resource.machine,
        subject=selected.device_id if selected is not None else choice.profile,
        label=selected.name if selected is not None else choice_label(choice),
        tier=COMPUTE_TIERS[choice.profile],
        approver=approver,
    )
    verdict = gate.check(call, gate.policy_of(project.settings), claims.get("a", ""))
    authorized = request.get("authorized_by")
    if isinstance(verdict, gate.Proposal):
        raise ForbiddenError(say("machineTierNotAllowed"))
    if choice.profile == "cloud":
        allocation_actor = (
            Actor(**authorized)
            if isinstance(authorized, dict)
            else Actor(handle=claims.get("a", ""), user_id=None, via="cheese")
        )
        try:
            machine = await MachineService(db).ensure_session_machine(
                session_id,
                actor=allocation_actor,
                choice=choice,
            )
        except CloudKeepsFailing as failing:
            await db.commit()
            return {"unavailable": str(failing)}
        if not machine.device_id or not hub.is_online(machine.device_id):
            await db.commit()
            return _Preparing(
                "云端工作电脑正在准备；对话和平台工具仍可用。",
                partial(_cloud_progress, db, hub, machine.id),
            )
        device_id = machine.device_id
        # Provisioning releases its transaction around external calls.
        topic = await TopicService(db).lock_for_execution(topic_id)
        row = await sessions.by_id(session_id, lock=True)
        if row is None or (row.execution_request or {}).get("generation") != generation:
            raise ConflictError("Execution request changed during provisioning")
    else:
        assert selected is not None
        device_id = selected.device_id
    # Placement records the session host before the screen that asks opens.
    assert row.runtime_location is not None
    host = row.runtime_location["device_id"]
    if device_id == host:
        raise ForbiddenError("Project tools cannot execute on the session host")
    # Each dialer reaches the backend over its own configured base.
    host_api = await device_api_base(db, host, settings.connector_public_base)
    api = await device_api_base(db, device_id, settings.connector_public_base)
    # Existing leases can outlive a deploy that changes the host's API address.
    # Ping/prepare must dial the current configured base, just like a new lease.
    if lease:
        lease = {
            **lease,
            "url": f"{host_api}/topics/{topic_id}/execution/session-{resource}",
        }
    work_resource = (lease or {}).get("resource_id") or generation
    claim = str(uuid.uuid4())
    reservation = {
        "kind": "device",
        "session_id": str(session_id),
        "generation": generation,
        "resource_id": work_resource,
        "room_resource_id": resource,
        "device_id": device_id,
        "status": "preparing",
        "claim": claim,
        "claim_until": (now + timedelta(seconds=660)).isoformat(),
    }
    # Every command start and file tool of the session comes through here, so
    # hands it already holds are re-checked many times a turn, while its other
    # calls — parallel tools, subagents, a running command being read — are in
    # flight on them. The execution route admits only a ready lease, so these
    # hands stay ready while they are re-checked: marking them preparing
    # refused every one of those calls for the length of the check.
    holding = (
        {
            **lease,
            "claim": reservation["claim"],
            "claim_until": reservation["claim_until"],
        }
        if lease is not None and ready and lease.get("device_id") == device_id
        else reservation
    )
    row.work_lease = holding
    actor = await user_by_handle(db, claims.get("a", ""))
    if actor is None:
        raise ForbiddenError("Execution actor no longer exists")
    actor_handle = actor.username
    project_id = topic.project_id
    await db.commit()
    execution_token = bind_resource_token(
        token, resource, session_id=str(session_id), lease_generation=generation
    )
    setup = _executor_env(
        env,
        api=api,
        token=execution_token,
        project_id=project_id,
        topic_id=topic_id,
        author=actor_handle,
        work_resource=work_resource,
    )
    try:
        info = await _start_executor(
            hub,
            lease,
            device_id=device_id,
            project_id=project_id,
            work_resource=work_resource,
            setup=setup,
        )
        target = {
            **reservation,
            "status": "ready",
            "home": device_home_dir(project_id, uuid.UUID(work_resource)),
            "state": info["state"],
            "release": info.get("release"),
            "upgrade_pending": info.get("upgrade_pending", False),
            "desired_release": info.get("desired_release"),
            "workspace": info["workspace"],
            "mcp_servers": info["mcp_servers"],
            "url": f"{host_api}/topics/{topic_id}/execution/session-{resource}",
        }
        target.pop("claim")
        target.pop("claim_until")
        if setup.get("CHEESE_ENVIRONMENT"):
            status = await environment_status(
                hub, device_id, project_id, uuid.UUID(work_resource)
            )
            if status["state"] != "ready":
                target["status"] = "preparing"
                target["environment_status"] = status
        if target["status"] == "ready":
            await execution.call(target, "ping", {}, hub=hub)
    except Exception as exc:
        # Setup is resumable at the same physical allocation; it is not a
        # dispatched model operation and must not create a replacement lease.
        row = await sessions.by_id(session_id, lock=True)
        if row and (row.work_lease or {}).get("claim") == claim:
            row.work_lease = {**holding, "claim_until": now.isoformat()}
        await db.commit()
        if isinstance(exc, DeviceOffline):
            # The machine went away while its executor was being set up: the
            # same answer as when it is away before setup starts (above).
            if choice.profile == "cloud":
                return _Preparing(
                    "云端工作电脑正在准备；对话和平台工具仍可用。",
                    partial(_cloud_progress, db, hub, machine.id),
                )
            return {"unavailable": "工作电脑未连接；对话和平台工具仍可用。"}
        raise
    current = await TopicService(db).lock_for_execution(topic_id)
    row = await sessions.by_id(session_id, lock=True)
    if (
        row is None
        or (row.work_lease or {}).get("claim") != claim
        or str(current.resource_id or current.id) != resource
    ):
        raise ConflictError("Execution allocation changed while preparing")
    row.work_lease = target
    if selected is not None:
        await tell_device_owner(
            db, topic=current, row=row, device=selected, lease=target
        )
    await db.commit()
    if target["status"] != "ready":
        message = "项目环境尚未就绪；对话和平台工具仍可用。"
        detail = {"environment_status": target.get("environment_status")}
        if (detail["environment_status"] or {}).get("state") in ENVIRONMENT_SETTLED:
            return {"unavailable": message, **detail}
        return _Preparing(
            message, _another_attempt, interval=ENVIRONMENT_POLL_S, detail=detail
        )
    return {"target": target, "token": execution_token}


async def _another_attempt() -> bool:
    return True


async def _claim_moved(db, session_id, claim) -> bool:
    """Another request of this session holds the installation; it has moved
    on once its claim is gone or has lapsed."""
    lease = await db.scalar(
        select(AgentSession.work_lease).where(AgentSession.id == session_id)
    )
    if (lease or {}).get("claim") != claim:
        return True
    until = lease.get("claim_until")
    return not until or datetime.fromisoformat(until) <= datetime.now(UTC)


async def _cloud_progress(db, hub, machine_id) -> str | bool:
    """Where the session's Cloud machine is on its way to being usable."""
    machine = (
        await db.execute(
            select(
                ProjectMachine.status,
                ProjectMachine.device_id,
                ProjectMachine.enroll_attempts,
                ProjectMachine.released_at,
                ProjectMachine.superseded_at,
            ).where(ProjectMachine.id == machine_id)
        )
    ).one_or_none()
    if machine is None or machine.released_at or machine.superseded_at:
        # The allocation changed under us; the next attempt says how.
        return True
    if machine.status == MachineStatus.error:
        if machine.device_id is None:
            # Failed while being built: the next attempt replaces it, within
            # the room's limit (``MachineService.ensure_session_machine``).
            return True
        # Enrolled, so the room's work may be on it: it is not replaced.
        return "云端工作电脑出错：供应方报告错误。对话和平台工具仍可用。"
    if machine.status in GONE:
        # Gone upstream: the next attempt forgets it and asks for another.
        return True
    if machine.device_id is None:
        if (machine.enroll_attempts or 0) >= MAX_ENROLL_ATTEMPTS:
            return (
                f"云端工作电脑接入失败：已尝试 {MAX_ENROLL_ATTEMPTS} 次。"
                "对话和平台工具仍可用。"
            )
        return False
    return hub.is_online(machine.device_id)
