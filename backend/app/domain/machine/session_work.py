"""Acquire one session's execution capability when a tool actually needs it."""

import asyncio
import json
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
from app.domain.agent import execution
from app.domain.agent.compute_configs import (
    ComputeChoice,
    machine_policy_call,
    room_choice,
    validate_choice,
)
from app.domain.agent.device_hub import DeviceCallError, DeviceOffline, device_hub
from app.domain.agent.device_provider import (
    _preview_ws_url,
    device_api_base,
    device_home_dir,
    environment_status,
)
from app.domain.agent.harness.claude_code import executor_launch as launch
from app.domain.agent.market import COMPUTE_TIERS
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
from app.domain.machine.services import MachineService
from app.domain.policy import gate
from app.domain.project.services import ProjectService
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

    A session works on the machine its lease names, or, before it holds one,
    the one its choice will lease (结论 60). A session with neither has not
    started working, and says so with ``choice: None``.
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


async def project_distribution(db, project_id) -> dict:
    """Where the project's agents that have started work are, right now.

    Counted per agent session in the project's open rooms: how many are on
    cloud, and how many on each self-hosted device, with whether an agent there
    can see the whole machine. A session that has not started working has no
    machine and is not counted: the project default decides where it goes.
    """
    from app.domain.topic.models import Topic, TopicStatus

    rows = await db.scalars(
        select(AgentSession)
        .join(Topic, Topic.id == AgentSession.topic_id)
        .where(Topic.project_id == project_id, Topic.status != TopicStatus.archived)
    )
    cloud = 0
    on_devices: dict[str | None, dict] = {}
    for row in rows:
        choice = (row.execution_request or {}).get("choice")
        if not choice:
            continue
        if choice.get("profile") == "cloud":
            cloud += 1
            continue
        device_id = (row.work_lease or {}).get("device_id") or choice.get("device_id")
        entry = on_devices.setdefault(
            device_id, {"device_id": device_id, "name": choice["name"], "agents": 0}
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
    listed.sort(key=lambda entry: (-entry["agents"], entry["name"]))
    return {"cloud": cloud, "devices": listed}


async def room_machine_visibility(
    db, topic, project_settings, sessions: list[dict]
) -> Visibility | None:
    """How much of a self-hosted machine the agents in this room can see.

    #282 / #358 原则八: whole-machine access is never granted silently, so the
    room shows it whenever any of its sessions has it (``session_machines``).
    A room where no session has asked for a machine yet answers for the choice
    its first session will be given.
    """
    started = [s for s in sessions if s["choice"] or s["lease"]]
    if started:
        seen = {s["visibility"] for s in started if s["visibility"] is not None}
    else:
        choice = room_choice(topic, project_settings)
        seen = set()
        if choice.profile == "device":
            visibility = await _visibility_of(sql_device_service(db), choice.device_id)
            if visibility is not None:
                seen.add(visibility)
    if Visibility.host in seen:
        return Visibility.host
    return next(iter(seen), None)


async def _agent_name(db, project, topic, handle: str) -> str:
    """The name a room shows for the agent on ``handle``: its saved teammate's,
    else the one the room falls back to (``topic_members``)."""
    from app.domain.agent_instance.services import AgentInstanceService

    seated = await AgentInstanceService(db).for_seat_handle(project, handle)
    if seated is None:
        seated = await TopicService(db).resolve_agent(topic)
    return seated.display_name


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
                "agent_handle": session.agent_handle,
                "agent_name": await _agent_name(
                    db, project, topic, session.agent_handle
                ),
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
    agent = await _agent_name(db, project, topic, row.agent_handle)
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
                "agentHandle": row.agent_handle,
                "agentName": agent,
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


class WorkComputerUnreachable(ConflictError):
    """The machine a session is leaving could not run the push before a switch.

    The one refusal a person may override (``abandon_unpushed``): the work on
    that machine stays there, and a switch made anyway leaves it behind."""


# How long a switch waits for the machine it leaves to push. The executor gives
# a command 120s and then reports it as still running (``runtime.bash``).
PUSH_WAIT_S = 150.0
PUSH_UNREACHABLE = "原来那台工作电脑连不上，无法推送改动，没有更换"


async def push_before_switch(lease: dict) -> None:
    """Push the session's work to its branches on the machine it is leaving.

    The same command a turn's Stop checkpoint runs there (``cheese sync
    --all``): every task checkout's commits go to its branch, and what is not
    committed is backed up as a snapshot ``cheese recover`` restores. Raises
    ``WorkComputerUnreachable`` when the command could not run at all, and a
    ``ConflictError`` with the machine's own words when it ran and failed.
    """
    if not device_hub.is_online(lease["device_id"]):
        raise WorkComputerUnreachable(PUSH_UNREACHABLE)
    try:
        result = await execution.call(
            lease,
            "control",
            {"subtype": "checkpoint", "request_id": f"switch-{uuid.uuid4()}"},
            hub=device_hub,
            timeout=PUSH_WAIT_S,
        )
    except (DeviceOffline, DeviceCallError, TimeoutError) as exc:
        raise WorkComputerUnreachable(f"{PUSH_UNREACHABLE}：{exc}") from exc
    if "error" in result:
        raise ConflictError(f"推送失败，没有更换：{result['error']}")
    output = result["value"]
    if output.get("backgroundTaskId"):
        raise ConflictError("推送两分钟内没有完成，没有更换；稍后重试")
    said = output.get("stdout") or ""
    if said.startswith("Exit code "):
        detail = said.partition("\n")[2].strip()[-600:] or said
        raise ConflictError(f"推送失败，没有更换：{detail}")


async def request_choice(
    db, *, topic_id, session_id, actor, choice, abandon_unpushed=False
):
    """Point one session at another work computer, after its work is pushed.

    The push runs on the machine the session leaves, with no transaction open.
    Only a person may switch without it, and only when that machine could not
    be reached (``abandon_unpushed``). A Cloud machine left after a push is
    deleted, so it stops counting against the team's quota.
    """
    topic = await TopicService(db).lock_for_execution(topic_id)
    row = await AgentSessionService(db).by_id(session_id, lock=True)
    if row is None or row.topic_id != topic_id:
        raise NotFoundError("Session not found")
    project = await ProjectService(db).get_or_404(topic.project_id)
    await validate_choice(db, topic.project_id, choice)
    if choice.profile == "device" and not choice.device_id:
        selected = await sql_device_service(db).first_healthy_device(
            topic.project_id, device_hub.is_online
        )
        if selected is None:
            raise ConflictError("选择一台工作电脑")
        choice.device_id = selected.device_id
    call = await machine_policy_call(db, project=project, topic=topic, choice=choice)
    verdict = gate.check(call, gate.policy_of(project.settings), actor.handle)
    if isinstance(verdict, gate.Proposal):
        raise ForbiddenError("所选机器超出项目允许的档位，请选择已授权的资源")
    if choice.profile == "cloud":
        await MachineService(db).require_use_authority(topic.project_id, actor)
    request = row.execution_request or {}
    old = row.work_lease
    previous = request.get("choice")
    if previous and ComputeChoice.model_validate(previous).model_dump(
        exclude={"name"}
    ) == choice.model_dump(exclude={"name"}):
        if choice.profile == "cloud" and not request.get("authorized_by"):
            row.execution_request = {**request, "authorized_by": asdict(actor)}
            await db.commit()
        return presentation(row)
    if old and old.get("status", "ready") != "ready":
        raise ConflictError("机器分配仍在进行，请稍后再换机")
    pushed = False
    if old:
        generation = request.get("generation")
        await db.commit()
        try:
            await push_before_switch(old)
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
            raise ConflictError("工作电脑刚被更换过，刷新后重试")
        if old.get("status", "ready") != "ready":
            raise ConflictError("机器分配仍在进行，请稍后再换机")
    left = None
    if (request.get("choice") or {}).get("profile") == "cloud":
        left = await MachineService(db).supersede_session_machine(
            session_id, actor=actor
        )
    # Whatever was on a Cloud machine is on its branches now (or it never held
    # a lease), so the machine goes. Anything else stays for the room's cleanup.
    release = left if left is not None and (pushed or not old) else None
    kept = [] if old is None or release is not None else [old]
    row.execution_request = {
        "generation": str(uuid.uuid4()),
        "choice": choice.model_dump(),
        "authorized_by": asdict(actor),
        "retained_leases": [*request.get("retained_leases", []), *kept],
    }
    row.work_lease = None
    await db.commit()
    if release is not None:
        await MachineService(db).release_left_machine(release.id)
    return presentation(row)


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
        "choice": room_choice(topic, project.settings).model_dump(),
        "authorized_by": None,
    }
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
    choice = ComputeChoice.model_validate(request["choice"])
    devices = sql_device_service(db)
    selected = None
    if choice.profile == "device":
        device_id = (lease or {}).get("device_id") or choice.device_id
        selected = (
            await devices.get_device(device_id)
            if device_id
            else await devices.first_healthy_device(topic.project_id, hub.is_online)
        )
        if selected is None or not hub.is_online(selected.device_id):
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
        label=selected.name if selected is not None else choice.name,
        tier=COMPUTE_TIERS[choice.profile],
        approver=approver,
    )
    verdict = gate.check(call, gate.policy_of(project.settings), claims.get("a", ""))
    authorized = request.get("authorized_by")
    if isinstance(verdict, gate.Proposal):
        raise ForbiddenError("所选机器超出项目允许的档位，请选择已授权的资源")
    if choice.profile == "cloud":
        allocation_actor = (
            Actor(**authorized)
            if isinstance(authorized, dict)
            else Actor(handle=claims.get("a", ""), user_id=None, via="cheese")
        )
        machine = await MachineService(db).ensure_session_machine(
            session_id,
            actor=allocation_actor,
            choice=choice,
        )
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
    row.work_lease = reservation
    actor = await user_by_handle(db, claims.get("a", ""))
    if actor is None:
        raise ForbiddenError("Execution actor no longer exists")
    actor_handle = actor.username
    project_id = topic.project_id
    await db.commit()
    execution_token = bind_resource_token(
        token, resource, session_id=str(session_id), lease_generation=generation
    )
    setup = {
        **{
            key: value
            for key, value in env.items()
            if key.startswith(("CHEESE_", "GIT_"))
        },
        "CHEESE_API": api,
        "CHEESE_TOKEN": execution_token,
        "CHEESE_PROJECT": str(project_id),
        "CHEESE_TOPIC": str(topic_id),
        "CHEESE_AUTHOR": actor_handle,
        "CHEESE_PREVIEW_URL": _preview_ws_url(api),
        "CHEESE_RESOURCE_ID": work_resource,
        "GIT_AUTHOR_NAME": actor_handle,
        "GIT_AUTHOR_EMAIL": f"{actor_handle}@agent.cheese.local",
    }
    try:
        info = None
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
                info = await execution.call(
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
        if info is None:
            installed = await hub.exec(
                device_id,
                ["python3", "-"],
                stdin=launch.script(project_id, uuid.UUID(work_resource), setup),
                timeout=660,
            )
            if installed.get("exit") != 0 or installed.get("truncated"):
                raise RuntimeError(installed.get("stderr") or "Executor setup failed")
            info = json.loads(installed["stdout"])
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
    except Exception:
        # Setup is resumable at the same physical allocation; it is not a
        # dispatched model operation and must not create a replacement lease.
        row = await sessions.by_id(session_id, lock=True)
        if row and (row.work_lease or {}).get("claim") == claim:
            row.work_lease = {**reservation, "claim_until": now.isoformat()}
            await db.commit()
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
        return "云端工作电脑创建失败：供应方报告错误。对话和平台工具仍可用。"
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
