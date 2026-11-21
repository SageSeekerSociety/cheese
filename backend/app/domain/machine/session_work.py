"""Acquire one session's execution capability when a tool actually needs it."""

import asyncio
import contextlib
import errno
import hashlib
import json
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Any

import httpx
from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import ConflictError, ForbiddenError, NotFoundError
from app.core.sandbox_auth import (
    bind_resource_token,
    for_a_machine,
    mint_environment_credential,
)
from app.core.sentences import say
from app.domain.addressing import REASON_MACHINE_OWNER, Addressed, Recipient
from app.domain.agent import execution
from app.domain.agent.device_hub import DeviceCallError, DeviceOffline, device_hub
from app.domain.agent.device_provider import (
    device_home_dir,
    environment_status,
)
from app.domain.agent.environment import VM, Sandbox, SelfHosted, spec_of
from app.domain.agent.environment import parse as parse_environment
from app.domain.agent.environment_failures import tell_failed
from app.domain.agent.harness.channel import ScreenSetupError, mint_session_token
from app.domain.agent.harness.claude_code import executor_launch as launch
from app.domain.agent.machine_address import device_api_base, site_forward, ws_url
from app.domain.agent.market import cloud_provisionable
from app.domain.agent.models import AgentTurn
from app.domain.agent.owner_provider import OWNER_CHANNEL
from app.domain.agent_instance.adapters import project_team
from app.domain.agent_instance.services import AgentInstanceService
from app.domain.agent_session import attachment as attachments
from app.domain.agent_session.models import AgentSession
from app.domain.agent_session.services import AgentSessionService
from app.domain.conversation.services import of_room, room_of
from app.domain.delivery.ledger import deliver
from app.domain.delivery_events import DeliveryEvent, event_id_for
from app.domain.device import sandbox as device_sandbox
from app.domain.device.supply import Supply
from app.domain.device.wiring import sql_device_service
from app.domain.environment import services as environment_rows
from app.domain.environment.models import Environment, EnvironmentAttachment
from app.domain.identity.actor import Actor
from app.domain.machine import holder as holders
from app.domain.machine import lease_claim, occupants, root_snapshots
from app.domain.machine.environments import device_for_spec, environment_of
from app.domain.machine.infra_records import keep_prepared, keep_refusal
from app.domain.machine.install_progress import InstallProgress, downloaded
from app.domain.machine.lease_claim import still_preparing
from app.domain.machine.lifecycle import (
    SandboxRemoving,
    frozen_sessions,
    run_on_host,
    thaw_if_asked,
    use_session_work,
)
from app.domain.machine.models import CloudHost, sandbox_image_ready
from app.domain.machine.progress import publish_line
from app.domain.machine.sandbox_images import (
    image_for_environment,
    image_for_session,
)
from app.domain.machine.sandbox_wait import (
    DISK_FULL,
    DISK_FULL_NOTICE,
    EXECUTOR_SETUP_FAILED,
    KEPT_NOT_LAID_BACK,
    SANDBOX_LOST,
    SANDBOX_LOST_KEPT,
    SANDBOX_PREPARING,
    SANDBOX_REPLACED,
    SANDBOX_RESUMED,
    STOPPED_PROCESSES,
    VM_PREPARING,
    _cloud_progress,
    _home_removed,
)
from app.domain.machine.services import (
    ACTIVE_STEP,
    CloudKeepsFailing,
    CloudPoolFull,
    HostPool,
)
from app.domain.notification.models import NotificationType
from app.domain.project.environment import EnvironmentConfig, pin_environment
from app.domain.project.models import Project
from app.domain.project.services import ProjectService
from app.domain.repository.kept_work import keeps_work, kept_ref, kept_work
from app.domain.room_task.models import Task
from app.domain.room_task.place import conversation_keeps_work
from app.domain.team.models import Team, TeamUserRelation
from app.domain.team.protocol import TeamMemberRole
from app.domain.topic.models import Topic, TopicKind
from app.domain.topic.services import TopicService
from app.domain.usage.compute import ComputeRefused
from app.domain.user.models import User
from app.domain.user.services import user_by_handle

logger = logging.getLogger(__name__)


def presentation(row, lease: dict | None):
    """A session and the machine it holds (``lease``, its environment), for
    the pages that show it. A sandbox on one of the platform's machines does
    not say which one: that is the platform's scheduling and nobody's to
    read."""
    shown = None
    if lease:
        online = device_hub.is_online(lease["device_id"])
        shown = (
            {"status": lease.get("status", "ready"), "online": online}
            if lease.get("platform")
            else {**lease, "online": online}
        )
    return {
        "id": str(row.id),
        "agent_handle": row.agent_handle,
        "harness": row.harness,
        "lease": shown,
    }


async def _whole_vm(db, device_id: str) -> bool:
    """Whether this device is a session's whole cloud VM."""
    return bool(
        await db.scalar(
            select(CloudHost.whole_machine).where(CloudHost.device_id == device_id)
        )
    )


async def _session_teammate(db, project, handle: str):
    """The saved teammate whose session is keyed ``handle``, or None.

    A session is keyed by its teammate's own handle, not by the seat it acts
    under, so this asks the same lookup that turns a session back into its
    author when it runs (``for_handle``). None for a handle that names no
    teammate (a room-derived one)."""

    try:
        return await AgentInstanceService(db).for_handle(project, handle)
    except NotFoundError:
        return None


async def _agent_name(db, project, topic, handle: str) -> dict:
    """The name a room shows for the agent whose session is keyed ``handle``:
    its saved teammate's, else the one the room falls back to. Beside it,
    whether that is still the name it was born with, which a screen shows in
    its reader's language."""

    teammate = await _session_teammate(db, project, handle)
    agent = (
        await AgentInstanceService(db).for_topic_by_id(topic.id)
        if teammate is None
        else AgentInstanceService.resolved(teammate)
    )
    return {
        "agent_name": agent.display_name,
        "agent_name_source": agent.name_source.value,
    }


async def screen_agent_name(
    db, project_id: uuid.UUID | None, topic_id: uuid.UUID | None, handle: str
) -> dict:
    """The name a screen's agent goes by, the way a room names it (see
    ``_agent_name``). Empty when the screen names no room that still exists;
    the page then shows the handle."""

    project = await db.get(Project, project_id) if project_id else None
    topic = await db.get(Topic, topic_id) if topic_id else None
    if project is None or topic is None:
        return {"agent_name": None, "agent_name_source": None}
    return await _agent_name(db, project, topic, handle)


async def _session_author(db, project, handle: str) -> str:
    """The seat the session keyed ``handle`` acts under: its teammate's, or the
    room-derived handle itself, which is its own seat."""

    teammate = await _session_teammate(db, project, handle)
    if teammate is None:
        return handle
    return await AgentInstanceService(db).ensure_identity(teammate)


async def session_author(db, project, handle: str) -> str:
    """``_session_author``, for the caller of a sandbox's platform-socket
    request (`domain.machine.caller`): what the session's launch credential
    names, and so what the request acts as."""
    return await _session_author(db, project, handle)


async def tell_device_owner(db, *, topic, row, device, lease, whole=False) -> None:
    """Tell a device's owner that an agent session started working on it,
    and whether it runs there over the whole machine (``whole``: a
    self-hosted environment) rather than in a sandbox.

    Notice only, no approval (#1900 step 5). One per session start: the event
    is the lease's work resource, so a later tool on the same lease, or the
    same lease prepared again, is the same event and reaches nobody twice. An
    owner who is a person in that room sees it on the room's roster already
    and is not told.
    """
    # deferred-import: tests replace this name on app.domain.topic_membership.services
    from app.domain.topic_membership.services import TopicMemberService

    in_room = await TopicMemberService(db).people_in(topic, team=project_team)
    admins = list(
        await db.scalars(
            select(User.username)
            .join(TeamUserRelation, TeamUserRelation.user_id == User.id)
            .where(
                TeamUserRelation.team_id == device.team_id,
                TeamUserRelation.deleted_at.is_(None),
                TeamUserRelation.role.in_([TeamMemberRole.OWNER, TeamMemberRole.ADMIN]),
            )
        )
    )
    recipients = tuple(
        Recipient(handle, REASON_MACHINE_OWNER)
        for handle in admins
        if handle not in in_room
    )
    if not recipients:
        return
    project = await ProjectService(db).get_or_404(topic.project_id)
    team = await db.get(Team, project.team_id)
    named = await _agent_name(db, project, topic, row.agent_handle)
    agent = named["agent_name"]
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
                "agentNameSource": named["agent_name_source"],
                "deviceId": device.device_id,
                "deviceName": device.name,
                "machineAccess": whole,
                # The sentence an email carries (`notification.maintenance`).
                "content": f"{agent} 开始在「{device.name}」上工作："
                f"{project.name} · {topic.title}"
                + ("，能访问整台机器" if whole else ""),
            },
            occurred_at=datetime.now(UTC),
        ),
        Addressed(recipients),
    )


class SessionWorking(ConflictError):
    """The session's room is mid-turn, and the switch was asked not to take a
    machine away from a turn (``if_idle``, the project's bulk switch)."""


# How long a switch, a room's cleanup or a VM's release waits for the
# checkpoint on the machine it leaves. The executor gives a command 120s and then
# reports it as still running (``runtime.bash``).
PUSH_WAIT_S = 150.0
WORKING = say("switchWhileWorking")


async def _room_is_working(db, topic_id) -> bool:

    running = await db.scalar(
        select(AgentTurn.id)
        .where(
            of_room(AgentTurn.conversation_id, topic_id),
            AgentTurn.stopped_at.is_(None),
        )
        .limit(1)
    )
    return running is not None


@dataclass(frozen=True)
class Restart:
    """How to start again a session's executor (`restart_executor`), and
    whose a command run there for the session is: the session itself, as a
    call it made would be named (``caller``, `routes/execution.py`)."""

    start: Callable[[], Awaitable[dict]]
    caller: dict


async def checkpoint(lease: dict, restart: Restart) -> bool:
    """One best-effort checkpoint on the machine a session is leaving.

    The same command a turn's Stop checkpoint runs there (``cheese sync
    --all``): every task checkout's unpushed commits go to its branch, and
    what it has not committed is kept beside the branch
    (``repository.kept_work``). Whatever happens — the machine is away, the
    command fails or outlasts ``PUSH_WAIT_S`` — the caller goes on: what a lost
    checkpoint loses is what changed since the last turn's checkpoint. Answers
    whether it ran to the end, for the log.

    The session's executor runs only while the session is in use, so ``start``
    brings it up the way a tool call would; one running an older release is
    started again too, since its ``cheese`` is the one it was installed with.
    """
    # A lease that never finished installing has no executor to run it.
    if not lease.get("state") or not _reachable(device_hub, lease["device_id"]):
        return False
    try:
        try:
            running = await execution.call(lease, "ping", {}, hub=device_hub)
        except DeviceCallError:
            running = None
        if running is None or not launch.can_prepare(running):
            lease = {**lease, "state": (await restart.start())["state"]}
        result = await execution.call(
            lease,
            "control",
            {
                "subtype": "checkpoint",
                "request_id": f"leave-{uuid.uuid4()}",
                # The session's own work it pushes: a push that fails is said
                # in the session's conversation, as its turn's would be.
                "caller": restart.caller,
            },
            hub=device_hub,
            timeout=PUSH_WAIT_S,
        )
    # Any failure at all, the machine's or ours: the caller does not wait on it.
    except Exception as exc:
        logger.warning(
            "checkpoint before leaving device %s failed: %s", lease["device_id"], exc
        )
        return False
    output = result.get("value") or {}
    said = output.get("stdout") or ""
    if (
        "error" in result
        or output.get("backgroundTaskId")
        or said.startswith("Exit code ")
    ):
        logger.warning(
            "checkpoint before leaving device %s did not finish: %s",
            lease.get("device_id"),
            result.get("error") or said[-600:],
        )
        return False
    return True


async def checkpoint_room(session, topic_id: uuid.UUID) -> None:
    """One best-effort checkpoint (``checkpoint``) of each of the room's
    sessions on its machine, all at once, before the room's cleanup stops them
    (``room_cleanup.retire``, which is handed this by whoever starts its sweep). The
    cleanup goes on whatever they answer: what a lost checkpoint does not push
    goes with the machine."""
    rows = (
        await session.execute(
            select(AgentSession, attachments.environment_column()).where(
                of_room(AgentSession.conversation_id, topic_id),
                attachments.is_attached(),
            )
        )
    ).all()
    # Frozen: checkpointed before it was frozen, and nothing ran since.
    frozen = await frozen_sessions(session, [row.id for row, _ in rows])
    starts = []
    # One checkpoint per environment: it pushes every checkout there,
    # whoever of the sessions in it made them.
    seen: set[tuple] = set()
    for row, environment in rows:
        if row.id in frozen:
            continue
        lease = environment or {}
        if lease.get("status", "ready") != "ready" or not lease.get("state"):
            continue
        where = (lease.get("device_id"), lease.get("resource_id"))
        if where in seen:
            continue
        seen.add(where)
        try:
            starts.append((lease, await restart_executor(session, row, lease)))
        except Exception:  # best effort; the cleanup goes on
            logger.warning("no checkpoint for session %s", row.id, exc_info=True)
    # No transaction is held while the machines run it.
    await session.commit()
    await asyncio.gather(*(checkpoint(lease, start) for lease, start in starts))


#: What names whose a command is. Never the executor's own environment: each
#: command is told by the call that started it (`runtime_core.identity`), from
#: the ``caller`` the execution route verified.
_CALLERS_OWN = frozenset({"CHEESE_ATTACHMENT", "CHEESE_TOPIC", "CHEESE_AUTHOR"})


def _executor_env(env, *, api, token, project_id, work_resource):
    """What a session's executor runs with: the caller's ``CHEESE_*``/``GIT_*``
    values, then the platform's own for this environment. Where it is, not who
    is asking (`_CALLERS_OWN`)."""
    site = site_forward(api)
    return {
        **{
            key: value
            for key, value in env.items()
            if key.startswith(("CHEESE_", "GIT_")) and key not in _CALLERS_OWN
        },
        "CHEESE_API": api,
        # The machine's copy, marked as held there (`sandbox_auth.for_a_machine`).
        # Blanked by the install wherever the machine has a platform socket
        # (`bootstrap.prepared`). Windows has none, and there this is the one
        # place an executor still holds a session's identity for all of its
        # commands: one home, one session, until a call can carry its own.
        "CHEESE_TOKEN": for_a_machine(token) if token else "",
        # Empty for a sandbox in no project (``holder``).
        "CHEESE_PROJECT": str(project_id or ""),
        "CHEESE_PREVIEW_URL": ws_url(api, "/preview/tunnel"),
        "CHEESE_RESOURCE_ID": work_resource,
        # No GIT_AUTHOR_*: a task's checkout sets its own author (`cheese
        # worktree`), and the environment would override it.
        **({"CHEESE_SITE_FORWARD": site} if site else {}),
    }


#: What the connector reports for a command that never exited on its own: it
#: was killed at the deadline it was given — a hung install, the common case —
#: or could not be started at all. A command that chose its own exit status
#: reports 0 or up, so this is never one of them (`cli/internal/host`).
NO_EXIT_STATUS = -1


#: The steps of an install the machine times (`bootstrap.configure_idle`):
#: staging the release, finding the pinned Claude Code, starting the sandbox,
#: and waiting for the executor's first answer. Parts of ``executor``.
INSTALL_STEPS = ("release", "claude", "sandbox", "ready")


def _install_steps(info: dict) -> dict[str, float]:
    """The install's own step times from what it answered; an executor that
    was only prepared, or a release that does not time them, answers none."""
    timings = info.get("timings")
    if not isinstance(timings, dict):
        return {}
    return {
        step: float(timings[step])
        for step in INSTALL_STEPS
        if isinstance(timings.get(step), int | float)
    }


async def _start_executor(
    hub,
    lease,
    *,
    device_id,
    namespace,
    work_resource,
    setup,
    sandbox,
    platform_machine,
    on_stderr=None,
):
    """Bring a session's executor up on ``device_id`` and answer what it
    reports: a running one on this release, in a sandbox exactly when asked,
    is prepared in place; anything else (none running, another release, the
    room's access to the machine changed) is installed, which starts it.

    ``sandbox``: whether it runs in a sandbox of its own, which is what a room
    ``isolated`` on the machine gets (`_sandboxed`). ``platform_machine``: a
    Cloud machine, where the install may add what a sandbox needs. Raises
    ``launch.SandboxRefused`` when the machine cannot give the room its
    sandbox. ``on_stderr`` hears what the install writes to stderr while it
    runs (`install_progress`).

    Ordinary calls are bounded by ``lease_claim.INSTALL_MOST_S``. An install
    restoring a root backup uses ``root_snapshots.RESTORE_TIMEOUT_S`` for the
    installation command, including download and unpack, with a longer claim.
    A caller can receive
    "preparing" while that installation continues. What failure leaves behind
    is answered by the caller.

    A machine that stopped answering is not only one that says nothing: the
    connector answers a command it had to kill with an ordinary result carrying
    ``NO_EXIT_STATUS``, not an error. Raised as the answered failure, that read
    as an install that ran and failed, and left the session on a host that never
    brought its executor up. The two are told apart here, and the caller answers
    the silent one as the machine it is.

    A lease names the machine it is held on, and only there do the hands it
    records exist. One that names another machine is not this attempt's hands:
    the attempt after a host was left — for a full disk, for a connector that
    went away — carries the lease of the machine just left, and dialing it would
    answer with that machine's own failure and have it recorded against the one
    this attempt is on (``_install``)."""
    if lease and lease.get("state") and lease.get("device_id") == device_id:
        try:
            running = await execution.call(
                lease, "ping", {}, hub=hub, timeout=lease_claim.INSTALL_MOST_S
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 500:
                raise
            running = {}
        except RuntimeError:
            # Installation resumes the same resource, never a tool call.
            running = {}
        if launch.can_prepare(
            running, sandbox, image=setup.get("CHEESE_SANDBOX_IMAGE")
        ):
            return await execution.call(
                lease,
                "prepare",
                # Built in a thread: it reads the project's skills off disk and
                # encodes them, on every tool call, and the event loop it would
                # otherwise hold is the one answering every other request.
                await asyncio.to_thread(
                    launch.payload_for,
                    namespace,
                    uuid.UUID(work_resource),
                    setup,
                    running.get("files"),
                    sandbox=sandbox,
                    platform_machine=platform_machine,
                ),
                hub=hub,
                timeout=lease_claim.INSTALL_MOST_S,
            )
    installed = await hub.exec(
        device_id,
        ["python3", "-"],
        stdin=await asyncio.to_thread(
            launch.script,
            namespace,
            uuid.UUID(work_resource),
            setup,
            sandbox=sandbox,
            platform_machine=platform_machine,
        ),
        timeout=(
            root_snapshots.RESTORE_TIMEOUT_S
            if setup.get("CHEESE_ROOT_SNAPSHOT")
            else lease_claim.INSTALL_MOST_S
        ),
        on_stderr=on_stderr,
    )
    if (refusal := launch.refused(installed)) is not None:
        raise refusal
    exit_status = installed.get("exit")
    if exit_status != 0 or installed.get("truncated"):
        raise ExecutorSetupFailed(
            installed.get("stderr") or "", killed=exit_status == NO_EXIT_STATUS
        )
    return json.loads(installed["stdout"])


class ExecutorSetupFailed(RuntimeError):
    """The install exited non-zero on the machine. Its stderr is the machine's
    own account of why; the agent is told its last line instead of a bare
    server error, so the reason reaches the room.

    ``killed``: the command never exited on its own (``NO_EXIT_STATUS``) — the
    connector killed it at the deadline it was given, or never got it started.
    That is not the install's own account of why it failed, and a shared host is
    answered as a machine to be placed away from rather than told about. What
    the connector leaves in ``stderr`` when it could not start the command is in
    there too, and goes unread on that path."""

    def __init__(self, stderr: str, *, killed: bool = False) -> None:
        super().__init__(stderr)
        self.killed = killed

    def reason(self) -> str:
        if f"[Errno {errno.ENOSPC}]" in str(self):
            return DISK_FULL
        # The download's progress lines are for the room's line
        # (`install_progress`), not an account of why it stopped.
        lines = [
            line.strip()
            for line in str(self).splitlines()
            if line.strip() and downloaded(line.strip()) is None
        ]
        return lines[-1] if lines else "Executor setup failed"


async def _sandboxed(environment: Sandbox | SelfHosted, db, device_id: str) -> bool:
    """Whether a session's executor on ``device_id`` runs in a sandbox of its
    own: whenever its environment is a sandbox, on the platform's machines and
    people's alike, except inside a whole VM of its own, where the VM is the
    sandbox. A self-hosted machine is the one place it runs without one."""
    if isinstance(environment, SelfHosted):
        return False
    return not await _whole_vm(db, device_id)


async def _platform_machine(db, device_id: str) -> bool:
    """Whether the platform provisioned ``device_id`` (a Cloud machine), so
    the install may add what a sandbox needs there."""
    device = await sql_device_service(db).get_device(device_id)
    return device is not None and device.supply is Supply.cloud


async def _launch_values(db, row, topic, project) -> tuple[str, dict]:
    """Who the session ``row`` acts as on its machine, and the variables its
    executor is set up with when no turn is handing them over: the room's
    environment, as a turn hands it over — the executor records the one it was
    installed with, and the next turn's start is refused while the two differ."""
    author = await _session_author(db, project, row.agent_handle)
    environment = (
        EnvironmentConfig().snapshot()
        if topic.kind == TopicKind.root
        else await pin_environment(db, project.id, topic.id)
    )
    return author, {"CHEESE_ENVIRONMENT": json.dumps(environment)}


async def restart_executor(db, row, lease) -> Restart:
    """How to start again the executor of the session ``row`` on the machine
    its ``lease`` is on, as a tool call there would: with the credential a
    session launches with, minted now, since the one it last ran with may have
    expired; and the session's name for what it runs there for the session."""
    topic = await TopicService(db).get_or_404(await room_of(db, row.conversation_id))
    project = await ProjectService(db).get_or_404(topic.project_id)
    task = (
        await db.get(Task, row.conversation_id)
        if row.conversation_id != topic.id
        else None
    )
    chosen = await environment_of(db, row, topic, task, project)
    author, values = await _launch_values(db, row, topic, project)
    resource = lease.get("room_resource_id") or str(topic.resource_id or topic.id)
    token = bind_resource_token(
        mint_session_token(project.id, row.conversation_id, author),
        resource,
        session_id=str(row.id),
        lease_generation=lease.get("generation"),
    )
    # A lease recorded before it carried its own resource is the generation's,
    # as `_attempt` reads it.
    work_resource = lease.get("resource_id") or lease.get("generation")
    api = await device_api_base(db, lease["device_id"], settings.connector_public_base)
    start = partial(
        _start_executor,
        device_hub,
        lease,
        device_id=lease["device_id"],
        namespace=project.id,
        work_resource=work_resource,
        sandbox=await _sandboxed(chosen, db, lease["device_id"]),
        platform_machine=await _platform_machine(db, lease["device_id"]),
        setup=_executor_env(
            {
                **values,
                "CHEESE_SANDBOX_IMAGE": await image_for_session(
                    db, lease["device_id"], row.id
                ),
            },
            api=api,
            token=token,
            project_id=project.id,
            work_resource=work_resource,
        ),
    )
    return Restart(
        start=start,
        caller={
            "attachment": str(row.id),
            "conversation": str(row.conversation_id),
            "author": author,
        },
    )


def _another_sandbox_wanted(environment: Sandbox | SelfHosted) -> bool:
    """Whether a session asking for the environment it already has is asking
    for another one anyway.

    True for one of the platform's sandboxes: the pool places those and
    replaces them freely, so a room whose sandbox is on a machine it cannot
    work in — its disk full, say — asks for the spec it is already on to be
    given a sandbox somewhere else, and answering "you are already there"
    leaves it on that machine for good. A sandbox on team devices keeps its
    existing directory, as does a session that chose one whole machine."""
    spec = spec_of(environment.spec) if isinstance(environment, Sandbox) else None
    return spec is not None and spec.platform


async def switch_environment(
    db,
    *,
    topic_id,
    session_id,
    actor,
    environment: Sandbox | SelfHosted,
    if_idle=False,
):
    """Give one agent another execution environment in its conversation,
    after one best-effort checkpoint on the machine it leaves.

    Only this agent moves: every agent in a conversation has its own. The
    checkpoint runs there with no transaction open, and the agent moves whether
    or not it went through (``checkpoint``). The sandbox it leaves is the idle
    sweep's to destroy (``HostPool.leave``). With ``if_idle`` a session
    whose room is mid-turn is left alone (``SessionWorking``). Whether the
    environment may be chosen at all was answered by the caller
    (``environments.check_allowed``).

    Asking for one of the platform's sandboxes that the session already has is
    a move, not a no-op (`_another_sandbox_wanted`): it is how a room gets off
    a sandbox it cannot work in, and the new one is placed away from the host
    the old one was on (`attachments.detach`)."""
    # 房间那一把锁：这一条会话的租约和它的执行环境拿着它读、拿着它写，别的请求
    # 看到的是「搬之前」或者「搬之后」，没有中间态。
    await TopicService(db).lock_for_execution(topic_id)
    sessions = AgentSessionService(db)
    row = await sessions.by_id(session_id, lock=True)
    if row is None or await room_of(db, row.conversation_id) != topic_id:
        raise NotFoundError("Session not found")
    current = await sessions.environment(row.conversation_id, row.agent_handle)
    if (
        current is not None
        and current.environment == environment.model_dump()
        and not _another_sandbox_wanted(environment)
    ):
        return
    held = await attachments.attachment_of(db, row.id, lock=True)
    old = held.environment if held is not None else None
    if still_preparing(old):
        raise ConflictError(say("machineAllocationInProgress"))
    if if_idle and await _room_is_working(db, topic_id):
        raise SessionWorking(WORKING)
    if old and await sql_device_service(db).get_device(old["device_id"]) is None:
        # The machine was unbound: nothing can reach it, and nothing left on it
        # can be cleaned up.
        old = None
    # A frozen sandbox is not checkpointed: it was before it was frozen, and
    # its stopped processes could not run `cheese sync` now.
    if old:
        generation = held.generation if held is not None else None
        if not await frozen_sessions(db, [session_id]):
            start = await restart_executor(db, row, old)
            await db.commit()
            await checkpoint(old, start)
        home = await occupants.home_of(db, session_id)
        if home is not None and home.sandbox_image:
            home.stopped_at = datetime.now(UTC)
            await db.commit()
            saved = await root_snapshots.save(
                db, home, old["device_id"], hub=device_hub, run_on_host=run_on_host
            )
            if saved is not None and saved["status"] == "superseded":
                raise ConflictError(say("workComputerJustSwitched"))
        await TopicService(db).lock_for_execution(topic_id)
        row = await sessions.by_id(session_id, lock=True)
        if row is None:
            raise NotFoundError("Session not found")
        held = await attachments.attachment_of(db, row.id, lock=True)
        old = held.environment if held is not None else None
        if held is None or held.generation != generation or not old:
            raise ConflictError(say("workComputerJustSwitched"))
        if still_preparing(old):
            raise ConflictError(say("machineAllocationInProgress"))
        if home is not None and home.sandbox_image:
            # The source is stopped; its detached home is now the idle sweep's.
            home.stopped_at = None
    on_platform = bool((old or {}).get("platform"))
    # The sandbox left behind by the last one to leave it is the idle
    # sweep's, whether or not it was ever handed to anyone; one somebody else
    # still works in stays theirs.
    leaving = held.environment_id if held is not None else None
    left = None
    if leaving is not None:
        alone = not await db.scalar(
            select(
                exists().where(
                    EnvironmentAttachment.environment_id == leaving,
                    EnvironmentAttachment.session_id != row.id,
                )
            )
        )
        pool = HostPool(db)
        left = await pool.leave(leaving) if alone else await pool.host_of(leaving)
    # A person's machine keeps the session's directories; the room's cleanup
    # finds them there by this lease.
    kept = [] if old is None or on_platform else [old]
    await sessions.set_environment(
        row.conversation_id,
        row.agent_handle,
        environment.model_dump(),
        chosen_by=asdict(actor),
    )
    # The next sandbox is not put back on the host this one came from
    # without naming it (`HostPool._moved_off`): the host it just left is the
    # full one, and a full host is what placement prefers.
    await attachments.detach(
        db,
        row,
        keep=bool(kept),
        left_host=left.id if left is not None else None,
        another=current is not None and current.environment == environment.model_dump(),
    )
    await db.commit()


# A tool that arrives while its session's machine is still being prepared
# waits for it, as a native session on that machine would simply run the
# command once it is up. One request waits at most this long and then answers
# that the machine is still preparing; the executor asks again until its own
# operation deadline. It is kept well inside the 300s the HTTP front gives any
# response.
PREPARING_WAIT_S = 50.0
# How long a request that will not wait for a machine being prepared may still
# take to be answered: one attempt at its hands, which a ready machine answers
# well inside this (dev, 2026-10: under 4 s). Past it the request answers that
# the machine is being prepared; holding it for the waiting bound instead kept
# a session that asked not to wait from starting for 50 s.
ANSWER_S = 10.0
# How often a waiting request looks at the signal it waits on. Each look is
# one row read and the hub's in-memory online set.
PREPARING_POLL_S = 1.0
# A project environment is looked at through the machine itself, which is a
# whole preparation attempt: that one is re-tried less often.
ENVIRONMENT_POLL_S = 5.0
# An environment in one of these states will not become ready by waiting.
ENVIRONMENT_SETTLED = {"failed", "stopped"}
# How long a machine that was checked end to end — pinged, prepared, its
# project environment read, pinged again (`_install`) — is handed out again
# without checking it (`_verified`). Every file tool and command start asks for
# hands, and that check is a whole round trip to the machine under the room's
# row lock: 1.2 s at the median on dev, and every call in flight in the room
# queued behind each other's. A failed call on those hands ends it at once
# (`distrust`), so what the window can leave unnoticed is only what does not
# fail a call: an executor release or a project environment that changed, and
# the setup a `prepare` refreshes. One minute is the step the sandbox's idle
# clock moves at anyway (`ACTIVE_STEP`, which `HostPool.holds` reads); in the
# 10-12..10-17 traces it lets 78% of machine tool calls skip the check (30 s:
# 67%, 120 s: 86%).
VERIFIED_FOR = ACTIVE_STEP


def _setup_digest(env: dict) -> str:
    """What the caller asked its executor to be set up with, as a lease
    records it: a session relaunched with other variables is checked again."""
    return hashlib.sha256(json.dumps(env, sort_keys=True).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class Attacher:
    """Who takes hands in an environment: an agent session, asking with its
    credential for one of its tool calls, or a person bringing a
    conversation's machine back for its files, with no turn. One path for
    both (`ensure`); what only an agent has stays the agent's — its
    credential's claims, the machine its session runs on, the choice it or
    its chooser made, what its agent is owed and told, and its device owner's
    notice."""

    #: The conversation the attacher works in through the environment; an
    #: agent's is read off its session's row, and this is what its
    #: credential claims.
    conversation_id: uuid.UUID | None
    #: An agent's session; None for a person.
    session_id: uuid.UUID | None = None
    #: A person's account; None for an agent.
    user_id: int | None = None
    #: Who a sandbox placed for this call is started for and paid by
    #: (``holder.require_use``): the person, or for an agent the one who
    #: chose its environment when not the session's own.
    actor: Actor | None = None
    #: An agent's credential, as presented and as verified.
    token: str = ""
    claims: dict = field(default_factory=dict)
    #: The variables its executor is set up with.
    env: dict = field(default_factory=dict)
    #: The caller hands a ``notice`` to the agent beside its tool's result.
    tells_agent: bool = False

    @classmethod
    def agent(
        cls,
        session_id: uuid.UUID,
        conversation_id: uuid.UUID | None,
        *,
        token: str,
        claims: dict,
        env: dict,
        tells_agent: bool = False,
    ) -> "Attacher":
        return cls(
            conversation_id=conversation_id,
            session_id=session_id,
            token=token,
            claims=claims,
            env=env,
            tells_agent=tells_agent,
        )

    @classmethod
    def person(cls, actor: Actor, conversation_id: uuid.UUID, env: dict) -> "Attacher":
        assert actor.user_id is not None  # a signed-in person
        return cls(
            conversation_id=conversation_id,
            user_id=actor.user_id,
            actor=actor,
            env=env,
        )


def _uuid_or_none(value) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None


async def _held_by(
    db, attacher: Attacher, attachment_id: uuid.UUID | None, *, lock: bool = False
) -> attachments.Attachment | None:
    """The attacher's attachment and the environment it is on, as they
    stand."""
    if attacher.session_id is not None:
        return await attachments.attachment_of(db, attacher.session_id, lock=lock)
    if attachment_id is None:
        return None
    return await attachments.person_attachment(db, attachment_id, lock=lock)


async def person_launch_values(db, conversation_id: uuid.UUID) -> dict:
    """The variables an executor a person brings up is set up with: the
    room's environment, as a turn there hands it over (`_launch_values`)."""
    room = await TopicService(db).get_or_404(await room_of(db, conversation_id))
    return {
        "CHEESE_ENVIRONMENT": json.dumps(
            await pin_environment(db, room.project_id, room.id)
        )
    }


async def _verified(db, *, topic_id, attacher: Attacher, hub):
    """The hands this session already holds, when they were checked within
    ``VERIFIED_FOR`` and nothing since says otherwise; None sends the caller
    through the whole check (`_attempt`), which also gives every refusal.

    Read without the room's lock, and writes nothing. That lock orders
    admissions that *write* a lease against archival (`writes.lock`): either
    the lease is recorded before the room is archived, and the room's cleanup
    stops it, or the admission sees the room archived. These hands are the
    lease already recorded, so whatever archival reads it reads with them on
    it; and the lock is released before the machine runs any call, so a call
    admitted the moment before archival commits runs after it here just as it
    does through the lock. An archived room, a rotated room resource (an
    unarchive), a notice owed to the agent about its sandbox (`Attachment.notice`:
    only the whole check hands it over, also when a person
    brought the machine back in between), a lease being re-checked or replaced (its ``verified_at`` is dropped, `_attempt`), a
    sandbox being destroyed or not touched for ``ACTIVE_STEP``, and a machine
    the hub does not reach are all left to the whole check."""
    if attacher.session_id is None:
        # A person's bring-up is checked whole every time.
        return None
    session_id, claims, token, env = (
        attacher.session_id,
        attacher.claims,
        attacher.token,
        attacher.env,
    )
    holder = await holders.look(db, topic_id)
    if holder is None:
        return None
    resource = holder.generation
    # The session's row, with whether the agent its credential names is still
    # a live account: the last thing asked below, in the same statement.
    found = (
        await db.execute(
            select(
                AgentSession,
                exists().where(
                    User.username == claims.get("a", ""), User.deleted_at.is_(None)
                ),
                *attachments.attachment_columns(),
            ).where(AgentSession.id == session_id)
        )
    ).first()
    if found is None:
        return None
    row, agent_live = found[0], found[1]
    held = attachments.attachment(found)
    lease = held.environment or {}
    location = row.runtime_location or {}
    verified = lease.get("verified_at")
    if (
        not verified
        or datetime.now(UTC) - datetime.fromisoformat(verified) >= VERIFIED_FOR
        or lease.get("kind") != "device"
        or lease.get("status", "ready") != "ready"
        or not lease.get("generation")
        or lease["generation"] != held.generation
        or lease.get("room_resource_id") != resource
        or lease.get("setup_digest") != _setup_digest(env)
        or held.notice.owed
        or lease.get("root_snapshot_notice")
        or claims.get("session") != str(row.id)
        or claims.get("t") != str(row.conversation_id)
        or not holder.signed_for(claims)
        or (
            location.get("channel") != OWNER_CHANNEL
            and lease.get("device_id") == location.get("device_id")
        )
        or not _reachable(hub, lease["device_id"])
    ):
        return None
    if await holders.holder_of(db, row.conversation_id) != topic_id:
        return None
    if lease.get("own") and (
        held.environment_id is None
        or not await HostPool(db, hub=hub).holds(
            held.environment_id, lease["device_id"]
        )
    ):
        return None
    if not agent_live:
        return None
    await db.commit()
    return {
        "target": lease,
        "token": holder.credential(
            token, session_id=str(session_id), lease_generation=lease["generation"]
        ),
    }


async def distrust(db, session_id: uuid.UUID, generation: str | None) -> None:
    """A call on the session's hands failed on the machine's side: the next
    request for them checks them whole (`_verified`). Only this session's row
    is taken, and only what says its hands were checked read and written — the execution route
    calls this, and it is served by the device connection owner
    (`device/owner_reads.py` says why it names its columns)."""
    await environment_rows.distrust(db, session_id, generation)
    await db.commit()


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


def _out_of_space(exc: BaseException) -> bool:
    """Whether this is a machine saying its disk or its supply of files ran out
    while the session's executor was being set up.

    Two ways that is said, and only these two are read: the report of an
    install that ran and could not get through (``ExecutorSetupFailed``, whose
    ``reason`` already names a machine that ran out of space — a machine that
    failed for any other reason is reported to the room and its host kept), and
    the machine's own answer to a call it could not carry out
    (``DeviceCallError``, here the ``prepare`` a room with no space left cannot
    write through)."""
    if isinstance(exc, ExecutorSetupFailed):
        return exc.reason() == DISK_FULL
    if isinstance(exc, DeviceCallError):
        return "[Errno 28]" in str(exc) or "No space left on device" in str(exc)
    return False


async def ensure(
    db,
    *,
    topic_id,
    wait_s,
    gone,
    hub=None,
    whole_check=False,
    attacher: Attacher | None = None,
    session_id=None,
    claims=None,
    token=None,
    env=None,
    tells_agent=False,
) -> dict[str, Any]:
    """Hands for the ``attacher`` — an agent session (given as ``attacher``,
    or by ``session_id`` with the ``claims``, ``token`` and ``env`` it asked
    with), or a person — waiting up to ``wait_s`` while they are prepared.

    ``gone`` reports that the caller has left (its command was stopped or
    timed out), which ends the wait without taking anything further.
    ``tells_agent``: the caller hands a ``notice`` in the answer to the agent
    beside its tool's result. Only such a caller is given one, and it is given
    once: a session whose sandbox was lost hears so on the first of its tool
    calls that has a new one, not on a hook's call that nobody reads.
    ``whole_check``: hands checked a moment ago are checked again end to end
    (`_verified` reads only the record), so an executor that stopped since is
    started again rather than handed out.
    """
    hub = hub or device_hub
    if attacher is None:
        assert session_id is not None and claims is not None
        attacher = Attacher.agent(
            session_id,
            _uuid_or_none(claims.get("t")),
            token=token or "",
            claims=claims,
            env=env or {},
            tells_agent=tells_agent,
        )
    held = (
        None
        if whole_check
        else await _verified(db, topic_id=topic_id, attacher=attacher, hub=hub)
    )
    if held is not None:
        return held
    clock = asyncio.get_running_loop().time
    deadline = clock() + wait_s
    while True:
        outcome = await _attempt(db, topic_id=topic_id, attacher=attacher, hub=hub)
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


async def _settle(db, holder, row, environment, *, topic_id):
    """The environment the session's next call works in, the session attached
    to it (`attachments.settle`): the conversation's, when the session's
    choice is the conversation's default; one of its own otherwise, which an
    agent on its owner's computer always has (it never takes the default,
    though its environment is the conversation's while nobody uses that)."""
    own_computer = (row.runtime_location or {}).get("channel") == OWNER_CHANNEL
    return await attachments.settle(
        db,
        row,
        choice=environment.model_dump(),
        default=(await holder.default_environment(db, row)).model_dump(),
        own=own_computer,
        where=environment_rows.Where(
            topic_id=topic_id,
            project_id=holder.project_id,
            generation=holder.generation,
            choice=environment.model_dump(),
        ),
    )


async def _settle_person(db, holder, attacher: Attacher, *, topic_id):
    """The environment a person's bring-up works in, the person attached to
    it (`attachments.settle_person`): where the conversation's work is.
    Answers the environment, its attachment's id, and the choice it is of,
    which is what is placed."""
    assert attacher.user_id is not None and attacher.conversation_id is not None
    default = (await holder.default_of(db, attacher.conversation_id)).model_dump()
    attached = await attachments.settle_person(
        db,
        user_id=attacher.user_id,
        conversation_id=attacher.conversation_id,
        default=default,
        where=environment_rows.Where(
            topic_id=topic_id,
            project_id=holder.project_id,
            generation=holder.generation,
            choice=default,
        ),
    )
    settled = await db.get(Environment, attached.environment_id)
    assert settled is not None  # the attachment's key
    return settled, attached.id, parse_environment(settled.choice)


async def attach_person(db, attacher: Attacher) -> uuid.UUID:
    """Attach a person to where the conversation's work is
    (`_settle_person`), under the holder's lock, and answer that
    environment. Flushes; the caller commits."""
    assert attacher.conversation_id is not None  # a person names it
    holder_id = await holders.holder_of(db, attacher.conversation_id)
    holder = await holders.lock_for_work(db, holder_id)
    if holder.project_id is None:
        raise NotFoundError("Conversation not found")
    settled, _, _ = await _settle_person(db, holder, attacher, topic_id=holder_id)
    return settled.id


async def _attempt(db, *, topic_id, attacher: Attacher, hub):
    """The row reservation survives worker death; remote work holds no DB lock."""
    holder = await holders.lock_for_work(db, topic_id)
    sessions = AgentSessionService(db)
    resource = holder.generation
    row = None
    if attacher.session_id is not None:
        session_id = attacher.session_id
        claims = attacher.claims
        row = await sessions.by_id(session_id, lock=True)
        if row is None or await holders.holder_of(db, row.conversation_id) != topic_id:
            raise NotFoundError("Session not found")
        if (
            claims.get("session") != str(row.id)
            or claims.get("t") != str(row.conversation_id)
            or not holder.signed_for(claims)
        ):
            raise ForbiddenError("Execution credential does not own this session")
        conversation_id = row.conversation_id
        environment = await holder.environment(db, row)
        location = row.runtime_location or {}
        settled = await _settle(db, holder, row, environment, topic_id=topic_id)
        attachment_id = row.id
    else:
        conversation_id = attacher.conversation_id
        assert conversation_id is not None  # a person names it
        if (
            holder.project_id is None
            or await holders.holder_of(db, conversation_id) != topic_id
        ):
            raise NotFoundError("Conversation not found")
        location = {}
        settled, attachment_id, environment = await _settle_person(
            db, holder, attacher, topic_id=topic_id
        )
    held = await _held_by(db, attacher, attachment_id, lock=True)
    assert held is not None and held.generation is not None  # settled above
    lease = held.environment
    attached = generation = held.generation
    ready = bool(lease and lease.get("status", "ready") == "ready")
    # ponytail: 本地只有一台机器，会话主机与工作电脑必然是同一台；生产护栏
    # 「项目文件别和执行会话挤在一台机器上」在这里只能挡下整个演示（agent 的
    # Bash/文件工具全被 403，wolfram 技能脚本跑不起来），所以 development 下放行。
    # 上限：会话与项目工具共用一个连接器，资源不再隔离。
    # 升级路径：注册第二台机器当工作电脑后，或部署在非 development，这条不再触发。
    single_machine = settings.environment == "development"
    if (
        row is not None
        # A member's own agent may work on the machine its session runs on,
        # its owner's, when that is where its environment is: the session then
        # sees the project where the executor holds it (`owner_provider`).
        and location.get("channel") != OWNER_CHANNEL
        and ready
        and (lease or {}).get("device_id") == location.get("device_id")
        and not single_machine
    ):
        raise ForbiddenError("Project tools cannot execute on the session host")
    now = datetime.now(UTC)
    if lease and lease.get("claim_until"):
        if datetime.fromisoformat(lease["claim_until"]) > now:
            await db.commit()
            return _Preparing(
                say("environmentPreparing"),
                partial(lease_claim.moved, db, settled.id, lease.get("claim")),
            )
    devices = sql_device_service(db)
    if lease and await devices.get_device(lease["device_id"]) is None:
        # Its machine was unbound (a re-bind enrols a new device id): nothing on
        # it can be reached again.
        if lease.get("platform"):
            # The pool's sandbox, if it is still there, is placed again below
            # and set up afresh.
            lease = None
        else:
            # A person's machine: the environment there is left for a new one.
            if row is not None:
                await attachments.abandon(db, row)
                settled = await _settle(db, holder, row, environment, topic_id=topic_id)
            else:
                await attachments.abandon_person(db, attachment_id)
                settled, attachment_id, environment = await _settle_person(
                    db, holder, attacher, topic_id=topic_id
                )
            held = await _held_by(db, attacher, attachment_id, lock=True)
            assert held is not None and held.generation is not None  # settled
            lease = held.environment
            attached = generation = held.generation
    spec = spec_of(environment.spec) if isinstance(environment, Sandbox) else None
    if isinstance(environment, Sandbox) and spec is None:
        await db.commit()
        return {"unavailable": str(say("environmentSpecGone"))}
    selected = None
    if isinstance(environment, SelfHosted):
        selected = await devices.get_device(environment.device_id)
        if selected is None:
            await db.commit()
            return {"unavailable": str(say("selfHostedUnbound"))}
        if not _reachable(hub, selected.device_id):
            await db.commit()
            return {"unavailable": str(say("selfHostedOffline", machine=selected.name))}
        if selected.supply != Supply.self_hosted:
            raise ForbiddenError("A self-hosted environment names a platform machine")
        if holder.project_id is None or not await devices.serves_project(
            selected.device_id, holder.project_id
        ):
            raise ForbiddenError("Device no longer serves this project")
        if await devices.get_hosted_device(selected.device_id) is None:
            raise ForbiddenError("Device is not hosted")
    elif spec is not None and not spec.platform:
        # Only a room's environment names a spec of people's own machines: what
        # serves it is the project's.
        assert isinstance(holder, holders.Room)
        selected = await device_for_spec(
            db,
            agent_handle=row.agent_handle if row is not None else None,
            spec=spec.name,
            project=await ProjectService(db).get_or_404(holder.project_id),
            task=await holders.task_in(db, holder, conversation_id),
            held=(lease or {}).get("device_id"),
            # A machine whose link dropped moments ago is called, and the call
            # waits for it (`_reachable`).
            is_online=partial(_reachable, hub),
        )
        if selected is None:
            await db.commit()
            return {"unavailable": str(say("specDevicesUnavailable"))}
    # The environment's own directory, wherever it is placed.
    work_resource = settled.resource_id
    if selected is not None and isinstance(environment, Sandbox):
        try:
            await HostPool(db, hub=hub).keep_on_device(
                settled.id,
                holder=holder,
                device_id=selected.device_id,
                spec=environment.spec,
                resource_id=work_resource,
            )
        except SandboxRemoving:
            # Its idle sandbox is being destroyed: the next attempt, once it is
            # gone, places it in a new one.
            await db.commit()
            return _Preparing(SANDBOX_PREPARING, partial(_home_removed, db, settled.id))
    cloud_host = None
    placed_s = None
    if selected is None and not cloud_provisionable(settings):
        await db.commit()
        return {"unavailable": str(say("platformSpecUnavailable"))}
    if selected is None:
        if row is not None:
            chosen = await sessions.environment(row.conversation_id, row.agent_handle)
            chooser = (chosen.chosen_by if chosen is not None else None) or None
            allocation_actor = attacher.actor or (
                Actor(**chooser)
                if isinstance(chooser, dict)
                else Actor(
                    handle=attacher.claims.get("a", ""), user_id=None, via="cheese"
                )
            )
        else:
            assert attacher.actor is not None  # a person
            allocation_actor = attacher.actor
        pool = HostPool(db, hub=hub)
        whole_machine = spec is not None and spec.name == VM
        placing = time.monotonic()
        try:
            cloud_host = await pool.place(
                settled.id,
                actor=allocation_actor,
                resource_id=work_resource,
                whole_machine=whole_machine,
            )
        except (CloudKeepsFailing, CloudPoolFull, ComputeRefused) as refused:
            if row is not None and not isinstance(refused, ComputeRefused):
                await keep_refusal(
                    db,
                    session_id=row.id,
                    conversation_id=row.conversation_id,
                    reason=(
                        "pool_full"
                        if isinstance(refused, CloudPoolFull)
                        else "provider_failing"
                    ),
                    said=refused.args[0],
                )
            await db.commit()
            return {"unavailable": str(refused)}
        except SandboxRemoving:
            # Its idle sandbox is being destroyed: the next attempt, once it is
            # gone, places it in a new one.
            await db.commit()
            return _Preparing(SANDBOX_PREPARING, partial(_home_removed, db, settled.id))
        placed_s = time.monotonic() - placing
        if (
            not cloud_host.device_id
            or not hub.is_online(cloud_host.device_id)
            or not sandbox_image_ready(cloud_host)
        ):
            line = await pool.tell_waiting(settled.id)
            await db.commit()
            await publish_line(topic_id, line)
            return _Preparing(
                VM_PREPARING if cloud_host.whole_machine else SANDBOX_PREPARING,
                partial(_cloud_progress, db, hub, cloud_host.id),
            )
        device_id = cloud_host.device_id
        # Provisioning releases its transaction around external calls.
        holder = await holders.lock_for_work(db, topic_id)
        if row is not None:
            row = await sessions.by_id(row.id, lock=True)
        now_held = (
            await _held_by(db, attacher, attachment_id, lock=True)
            if row is not None or attacher.session_id is None
            else None
        )
        now_attached = now_held.generation if now_held is not None else None
        if (
            now_attached != attached
            or (row is None and attacher.session_id is not None)
            or (row is not None and await holder.environment(db, row) != environment)
            or (now_held is not None and now_held.environment_id != settled.id)
        ):
            raise ConflictError("Execution request changed during provisioning")
        # Placing a new home commits (`HostPool._place_on_free`), and another
        # attempt in this environment — a person's, a parallel tool call — may
        # have taken its claim meanwhile, or finished with it. The claim is
        # asked again under the lock that writes it, and the environment read
        # as it is now: two installations in one environment left the first
        # one's claim overwritten, and it failed once it was done.
        lease = now_held.environment if now_held is not None else None
        if lease and lease.get("claim_until"):
            if datetime.fromisoformat(lease["claim_until"]) > now:
                await db.commit()
                return _Preparing(
                    say("environmentPreparing"),
                    partial(lease_claim.moved, db, settled.id, lease.get("claim")),
                )
        ready = bool(lease and lease.get("status", "ready") == "ready")
    else:
        device_id = selected.device_id
    if row is not None:
        # Placement records the session host before the screen that asks opens.
        assert row.runtime_location is not None
        host = row.runtime_location["device_id"]
        if (
            device_id == host
            and row.runtime_location.get("channel") != OWNER_CHANNEL
            and not single_machine
        ):
            raise ForbiddenError("Project tools cannot execute on the session host")
    else:
        # A person has no session host: the address recorded is the one the
        # platform's own session host dials, as an agent's there would.
        host = settings.agent_session_device_id or device_id
    # Each dialer reaches the backend over its own configured base.
    host_api = await device_api_base(db, host, settings.connector_public_base)
    api = await device_api_base(db, device_id, settings.connector_public_base)
    sandbox = await _sandboxed(environment, db, device_id)
    platform_machine = await _platform_machine(db, device_id)
    # Existing leases can outlive a deploy that changes the host's API address.
    # Ping/prepare must dial the current configured base, just like a new lease.
    if lease:
        lease = {
            **lease,
            "url": (
                f"{host_api}/topics/{conversation_id}/execution/session-{resource}"
            ),
        }
    claim = str(uuid.uuid4())
    reservation = {
        "kind": "device",
        **({"session_id": str(row.id)} if row is not None else {}),
        "generation": generation,
        "resource_id": work_resource,
        "room_resource_id": resource,
        "device_id": device_id,
        # On one of the platform's machines, which nobody is shown.
        "platform": cloud_host is not None,
        "status": "preparing",
        "claim": claim,
        "claim_until": (now + timedelta(seconds=lease_claim.CLAIM_TTL_S)).isoformat(),
    }
    # Every command start and file tool of the session comes through here, so
    # hands it already holds are re-checked many times a turn, while its other
    # calls — parallel tools, subagents, a running command being read — are in
    # flight on them. The execution route admits only a ready lease, so these
    # hands stay ready while they are re-checked: marking them preparing
    # refused every one of those calls for the length of the check.
    # While they are re-checked they are not handed out unchecked
    # (`_verified`): the check is running because something may have changed.
    holding = (
        {
            **{key: value for key, value in lease.items() if key != "verified_at"},
            "claim": reservation["claim"],
            "claim_until": reservation["claim_until"],
        }
        if lease is not None and ready and lease.get("device_id") == device_id
        else reservation
    )
    await attachments.attach(db, attachment_id, holding)
    # A bring-up somebody asked for starts again here: whatever stopped the
    # last one is over.
    settled.failure = None
    if (
        row is not None
        and await user_by_handle(db, attacher.claims.get("a", "")) is None
    ):
        raise ForbiddenError("Execution actor no longer exists")
    await db.commit()
    # The claim is taken; what installs under it runs as this process's own
    # task on its own session, and the request only waits for it. A request
    # that stops waiting — its time ran out (the route answers "preparing"),
    # its caller left — leaves the installation running, and the claim ends
    # with it: handed over as ready, lapsed when it fails, or left to lapse
    # unrenewed when this process goes. Cancelled with the request instead, it
    # left the claim standing for its full cap with nobody installing, and
    # every start of the session failed until then.
    work = asyncio.ensure_future(
        _install(
            db.bind,
            attacher=attacher,
            attachment_id=attachment_id,
            token=attacher.token,
            resource=resource,
            environment_id=settled.id,
            generation=generation,
            env=attacher.env,
            api=api,
            host_api=host_api,
            holder=holder,
            # The executor and its progress lines belong to the conversation;
            # its machine and lock to the holder.
            topic_id=conversation_id,
            work_resource=work_resource,
            hub=hub,
            lease=lease,
            device_id=device_id,
            sandbox=sandbox,
            platform_machine=platform_machine,
            reservation=reservation,
            holding=holding,
            claim=claim,
            now=now,
            cloud_host_id=cloud_host.id if cloud_host is not None else None,
            cloud_vm=cloud_host is not None and cloud_host.whole_machine,
            owner_device=selected is not None,
            own=isinstance(environment, Sandbox),
            # Timed only when the call brings up hands it did not hold: a new
            # sandbox, one another host now has, or one woken from a freeze
            # (`infra_records.keep_prepared`). A re-check of ready hands is not.
            fresh=holding is reservation,
            placed_s=placed_s,
        )
    )
    _INSTALLS.add(work)
    work.add_done_callback(_installed)
    outcome = await asyncio.shield(work)
    if isinstance(outcome, _CloudPreparing):
        assert cloud_host is not None
        return _Preparing(
            VM_PREPARING if cloud_host.whole_machine else SANDBOX_PREPARING,
            partial(_cloud_progress, db, hub, cloud_host.id),
        )
    return outcome


async def environment_credential(
    db,
    holder,
    *,
    conversation_id: uuid.UUID,
    environment_id: uuid.UUID,
    lease_generation: str | None,
    by: str | None = None,
    ttl_s: int | None = None,
) -> str:
    """The credential minted for an attachment that is no agent — a
    person's — or for the environment itself: the environment's authority in
    ``conversation_id``, never a person's own (`mint_environment_credential`).
    A 支线's conversation keeps no work, so its environment's credential
    never pushes."""
    if holder.project_id is None:
        raise ForbiddenError("An environment in no project acts for nobody")
    return mint_environment_credential(
        project_id=str(holder.project_id),
        conversation_id=str(conversation_id),
        resource_id=holder.generation,
        environment_id=str(environment_id),
        lease_generation=lease_generation,
        scratch=not await conversation_keeps_work(db, conversation_id),
        by=by,
        **({"ttl_s": ttl_s} if ttl_s is not None else {}),
    )


#: Installations a request started and may have stopped waiting for. Held
#: here so the event loop keeps them until they end.
_INSTALLS: set[asyncio.Future] = set()


class _CloudPreparing:
    """What `_install` answers when a Cloud machine dropped during setup; the
    request turns it into a wait on its own environment."""


_CLOUD_PREPARING = _CloudPreparing()


def _installed(work: asyncio.Future) -> None:
    _INSTALLS.discard(work)
    if not work.cancelled() and work.exception() is not None:
        # Whoever asked may have stopped waiting; the claim has already lapsed
        # (`_install`), so the next attempt starts over.
        logger.warning("executor installation failed", exc_info=work.exception())


async def _install(
    bind,
    *,
    attacher: Attacher,
    attachment_id,
    token,
    resource,
    environment_id,
    generation,
    env,
    api,
    host_api,
    holder,
    topic_id,
    work_resource,
    hub,
    lease,
    device_id,
    sandbox,
    platform_machine,
    reservation,
    holding,
    claim,
    now,
    cloud_host_id,
    # Whether the host is the session's own VM rather than a shared one. False
    # by default, which is the case that may be given up: `leave_unusable`
    # refuses a whole machine on its own (`services.leave_unusable`).
    cloud_vm=False,
    owner_device,
    own,
    fresh=False,
    placed_s=None,
) -> dict | _Preparing | _CloudPreparing:
    """Bring the environment's executor up under ``claim`` for the
    ``attacher`` and record the outcome."""
    session_id = attacher.session_id
    tells_agent = attacher.tells_agent and session_id is not None
    async with AsyncSession(bind, expire_on_commit=False) as db:
        sessions = AgentSessionService(db)
        if session_id is not None:
            execution_token = holder.credential(
                token, session_id=str(session_id), lease_generation=generation
            )
        else:
            execution_token = await environment_credential(
                db,
                holder,
                conversation_id=topic_id,
                environment_id=environment_id,
                lease_generation=generation,
                by=attacher.actor.handle if attacher.actor is not None else None,
            )
        namespace = holder.namespace
        setup = _executor_env(
            env,
            api=api,
            token=holder.sandbox_token(execution_token),
            project_id=holder.project_id,
            work_resource=work_resource,
        )
        if platform_machine and sandbox:
            setup["CHEESE_SANDBOX_IMAGE"] = await image_for_environment(
                db, device_id, environment_id
            )
            await db.commit()
        root_snapshot = (
            await root_snapshots.root_to_restore(db, environment_id, session_id)
            if platform_machine and sandbox and setup.get("CHEESE_SANDBOX_IMAGE")
            else None
        )
        if root_snapshot and "url" in root_snapshot:
            setup["CHEESE_ROOT_SNAPSHOT"] = json.dumps(root_snapshot)
        disk_full_mb = None
        # On a person's own computer the room sees the runtime download
        # (`install_progress`); a Cloud machine's getting ready is the
        # platform's own, kept out of the conversation (`progress`).
        shown = (
            InstallProgress(bind, conversation_id=topic_id, device_id=device_id)
            if owner_device
            else contextlib.nullcontext()
        )
        started = time.monotonic()
        thaw_s = None
        install_steps: dict[str, float] = {}
        try:
            claim_most_s = lease_claim.CLAIM_MOST_S + (
                root_snapshots.RESTORE_TIMEOUT_S - lease_claim.INSTALL_MOST_S
                if setup.get("CHEESE_ROOT_SNAPSHOT")
                else 0
            )
            async with lease_claim.kept(
                bind, environment_id, claim, since=now, most_s=claim_most_s
            ):
                thawed = await thaw_if_asked(
                    db,
                    hub,
                    environment_id=environment_id,
                    device_id=device_id,
                    work_resource=work_resource,
                )
                if thawed:
                    thaw_s = time.monotonic() - started
                executor_from = time.monotonic()
                async with shown as heard:
                    info = await _start_executor(
                        hub,
                        lease,
                        device_id=device_id,
                        namespace=namespace,
                        work_resource=work_resource,
                        setup=setup,
                        sandbox=sandbox,
                        platform_machine=platform_machine,
                        on_stderr=heard,
                    )
                executor_s = time.monotonic() - executor_from
                install_steps = _install_steps(info)
                target = {
                    **reservation,
                    "status": "ready",
                    "home": device_home_dir(namespace, uuid.UUID(work_resource)),
                    "state": info["state"],
                    "release": info.get("release"),
                    "upgrade_pending": info.get("upgrade_pending", False),
                    "desired_release": info.get("desired_release"),
                    "workspace": info["workspace"],
                    "mcp_servers": info["mcp_servers"],
                    # Whether nothing but this session works in what the
                    # executor can write: a sandbox of its own, a whole VM
                    # included. The executor route lets a session whose work is
                    # not kept run freely only there (`routes/execution.py`).
                    "own": own,
                    "url": f"{host_api}/topics/{topic_id}/execution/session-{resource}",
                }
                root_status = (info.get("root_snapshot") or {}).get("status")
                if (
                    fresh
                    and root_snapshot
                    and root_snapshot.get("status") == "too_large"
                ):
                    root_status = "too_large"
                root_notice = root_snapshots.NOTICES.get(root_status or "") or (
                    lease or {}
                ).get("root_snapshot_notice")
                if root_notice:
                    target["root_snapshot_notice"] = root_notice
                target.pop("claim")
                target.pop("claim_until")
                disk_full_mb = info.get("disk_full_mb")
                if setup.get("CHEESE_ENVIRONMENT"):
                    status = await environment_status(
                        hub, device_id, namespace, uuid.UUID(work_resource)
                    )
                    if status["state"] != "ready":
                        target["status"] = "preparing"
                        target["environment_status"] = status
                if target["status"] == "ready":
                    # Bounded like the install: an executor that installed and
                    # then will not answer is a machine this attempt gives up on
                    # too, not one that holds the call that asked for its hands.
                    await execution.call(
                        target, "ping", {}, hub=hub, timeout=lease_claim.INSTALL_MOST_S
                    )
        except Exception as exc:
            # Setup is resumable at the same physical allocation; it is not a
            # dispatched model operation and must not create a replacement lease.
            held = await _held_by(db, attacher, attachment_id, lock=True)
            if held and (held.environment or {}).get("claim") == claim:
                await attachments.attach(
                    db, attachment_id, {**holding, "claim_until": now.isoformat()}
                )
            await db.commit()
            if isinstance(exc, launch.SandboxRefused):
                # The machine cannot make a sandbox and said why, in words for
                # its owner. That is a fact about the machine, kept where its
                # owner reads it; no sandbox is placed there again, and the next
                # attempt places this one on another device of its spec.
                await sql_device_service(db).record_sandbox_check(
                    device_id, isolates=False, reason=device_sandbox.kept(exc.said)
                )
                await db.commit()
                return _Preparing(say("environmentPreparing"), _another_attempt)
            # The host could not bring the session's executor up: its connector
            # went away, the install outlasted the deadline every call into a
            # machine is given (`_start_executor`), or the connector killed the
            # install at that deadline and reported no exit status for it. A
            # whole VM is the session's own, waited for where it is; a shared
            # host is left when the pool has another that can run the sandbox,
            # so the next attempt is placed there instead of being pinned to
            # this one for the rest of the tool call.
            away = isinstance(exc, (DeviceOffline, TimeoutError)) or (
                isinstance(exc, ExecutorSetupFailed) and exc.killed
            )
            if away and cloud_host_id is not None:
                pool = HostPool(db, hub=hub)
                moved = not cloud_vm and await pool.leave_unusable(
                    environment_id, cloud_host_id
                )
                await db.commit()
                if moved:
                    return _Preparing(SANDBOX_PREPARING, _another_attempt)
                return _CLOUD_PREPARING
            if _out_of_space(exc):
                # A write refused for want of space. On a host the sandbox's
                # home is held to a quota of its own (`sandbox_host.py`), so the
                # space that ran out is the session's: another host would give
                # it an empty home and none of its work. The room is told, and
                # its agent can make room where it is. A host whose disk as a
                # whole runs short is the disk guard's to mark.
                return {"unavailable": EXECUTOR_SETUP_FAILED.format(reason=DISK_FULL)}
            if isinstance(exc, ExecutorSetupFailed):
                logger.warning("executor installation failed: %s", exc)
                return {
                    "unavailable": EXECUTOR_SETUP_FAILED.format(reason=exc.reason())
                }
            if isinstance(exc, ScreenSetupError):
                # The machine answered, but could not say what it was asked
                # (the project environment's state did not read there). Its
                # sentence is written for the agent, and the next attempt asks
                # again.
                logger.warning("executor installation failed: %s", exc)
                return {"unavailable": str(exc)}
            if isinstance(exc, DeviceOffline):
                # The machine went away while its executor was being set up, and
                # is nobody's Cloud host: the same answer as when it is away
                # before setup starts (above).
                return {"unavailable": str(say("environmentDeviceOffline"))}
            raise
        current = await holders.lock_for_work(db, holder.id)
        row = (
            await sessions.by_id(session_id, lock=True)
            if session_id is not None
            else None
        )
        held = await _held_by(db, attacher, attachment_id, lock=True)
        if (
            (row is None and session_id is not None)
            or held is None
            or (held.environment or {}).get("claim") != claim
            or current.generation != resource
        ):
            raise ConflictError("Execution allocation changed while preparing")
        if target["status"] == "ready":
            # Checked end to end just now: what `_verified` hands out again.
            target["verified_at"] = datetime.now(UTC).isoformat()
            target["setup_digest"] = _setup_digest(env)
        root_notice = (
            target.pop("root_snapshot_notice", None)
            if tells_agent and target["status"] == "ready"
            else None
        )
        await attachments.attach(db, attachment_id, target)
        notice = (
            attachments.take_notice(row)
            if row is not None and tells_agent and target["status"] == "ready"
            else attachments.Notice()
        )
        lost = notice.lost
        resumed = not lost and notice.parked
        stopped = list(notice.stopped)
        conversation_id = topic_id
        # Only an agent's start is told its device's owner: a person bringing
        # the machine back for its files is not (`tell_device_owner`).
        device = (
            await sql_device_service(db).get_device(device_id)
            if owner_device and row is not None
            else None
        )
        if device is not None and row is not None:
            # Only a room's sessions work on people's own machines.
            await tell_device_owner(
                db,
                topic=await TopicService(db).get_or_404(holder.id),
                row=row,
                device=device,
                lease=target,
                whole=not own,
            )
        ready_line = None
        if cloud_host_id is not None and target["status"] == "ready":
            if fresh or thaw_s is not None:
                await keep_prepared(
                    db,
                    conversation_id=conversation_id,
                    session_id=session_id,
                    stages={
                        "place": placed_s,
                        "thaw": thaw_s,
                        "executor": executor_s,
                        **install_steps,
                    },
                    total_s=time.monotonic() - started + (placed_s or 0),
                    waited_s=await HostPool(db, hub=hub).waited_s(environment_id),
                )
            ready_line = await HostPool(db, hub=hub).tell_ready(environment_id)
        await db.commit()
        await publish_line(topic_id, ready_line)
        if target["status"] != "ready":
            message = "项目环境尚未就绪；对话和平台工具仍可用。"
            detail = {"environment_status": target.get("environment_status")}
            settled = detail["environment_status"] or {}
            if settled.get("state") in ENVIRONMENT_SETTLED:
                if settled.get("state") == "failed":
                    # The people waiting are told where they wait, with the
                    # way to the settings; the teammate is told here.

                    line = await tell_failed(
                        db,
                        room_id=topic_id,
                        conversation_id=conversation_id,
                        status=settled,
                    )
                    await db.commit()
                    if line is not None:
                        await publish_line(conversation_id, line)
                return {"unavailable": message, **detail}
            return _Preparing(
                message, _another_attempt, interval=ENVIRONMENT_POLL_S, detail=detail
            )
        lost_notice = (
            await _lost_notice(db, holder.project_id, conversation_id)
            if holder.project_id and lost
            else SANDBOX_LOST
            if holder.project_id
            else SANDBOX_REPLACED
        )
        notices = [lost_notice] * bool(lost) + [SANDBOX_RESUMED] * bool(resumed)
        if root_notice:
            notices.append(root_notice)
        if stopped:
            notices.append(
                STOPPED_PROCESSES.format(
                    processes="\n".join(f"  {one}" for one in stopped)
                )
            )
        if disk_full_mb is not None:
            notices.append(DISK_FULL_NOTICE.format(mb=disk_full_mb))
        return {
            "target": target,
            "token": execution_token,
            **({"notice": "\n".join(notices)} if notices else {}),
            **({"attachment": str(attachment_id)} if session_id is None else {}),
        }


async def _lost_notice(db, project_id: uuid.UUID, conversation_id: uuid.UUID) -> str:
    """What a session of a project is told on its first call in a new sandbox:
    whether its task's uncommitted work comes back with the checkout
    (`repository.kept_work`), and when it does not, where it is. A forge that
    cannot say is not asked again: the notice says what is kept as a rule."""
    try:
        if not await keeps_work(project_id, db):
            return SANDBOX_LOST
        task = (
            await db.execute(
                select(Task.branch_name, Task.base_branch).where(
                    Task.id == conversation_id
                )
            )
        ).first()
        kept = (
            await kept_work(project_id, db, conversation_id, task[0], task[1])
            if task is not None and task[0]
            else None
        )
    except Exception:  # the notice goes out without what it could not read
        logger.warning(
            "reading the kept work of %s failed", conversation_id, exc_info=True
        )
        return SANDBOX_LOST_KEPT
    if kept is None or kept.laid_back:
        return SANDBOX_LOST_KEPT
    return SANDBOX_LOST_KEPT + KEPT_NOT_LAID_BACK.format(
        at=kept.stamp(), ref=kept_ref(conversation_id), commit=kept.commit[:12]
    )


async def _another_attempt() -> bool:
    return True


# Freezing a sandbox runs this module's checkpoint first; `lifecycle` cannot
# import this module (it is imported by it), so it is handed the two it uses.
use_session_work(checkpoint=checkpoint, restart_executor=restart_executor)
