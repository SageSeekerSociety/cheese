"""Acquire one session's execution capability when a tool actually needs it."""

import json
import uuid
from dataclasses import asdict
from datetime import UTC, datetime, timedelta

import httpx

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
from app.domain.agent.device_hub import device_hub
from app.domain.agent.device_provider import (
    _preview_ws_url,
    device_api_base,
    device_home_dir,
    environment_status,
)
from app.domain.agent.harness.claude_code import executor_launch as launch
from app.domain.agent.market import COMPUTE_TIERS
from app.domain.agent_session.services import AgentSessionService
from app.domain.device.supply import Supply, has_runnable_transport
from app.domain.device.wiring import sql_device_service
from app.domain.identity.actor import Actor
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


async def request_choice(db, *, topic_id, session_id, actor, choice):
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
            raise ConflictError("请选择一台工作机器")
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
    if (request.get("choice") or {}).get("profile") == "cloud":
        await MachineService(db).supersede_session_machine(session_id, actor=actor)
    row.execution_request = {
        "generation": str(uuid.uuid4()),
        "choice": choice.model_dump(),
        "authorized_by": asdict(actor),
        "retained_leases": [
            *request.get("retained_leases", []),
            *([old] if old else []),
        ],
    }
    row.work_lease = None
    await db.commit()
    return presentation(row)


async def ensure(db, *, topic_id, session_id, claims, token, env, hub=None):
    """The row reservation survives worker death; remote work holds no DB lock."""
    hub = hub or device_hub
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
            return {"unavailable": "工作机器正在准备；对话和平台工具仍可用。"}
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
            return {"unavailable": "工作机器未连接；对话和平台工具仍可用。"}
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
            return {"unavailable": "Cloud 机器正在准备；对话和平台工具仍可用。"}
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
    await db.commit()
    if target["status"] != "ready":
        return {
            "unavailable": "项目环境尚未就绪；对话和平台工具仍可用。",
            "environment_status": target.get("environment_status"),
        }
    return {"target": target, "token": execution_token}
