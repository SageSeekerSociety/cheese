"""Acquire one session's execution capability when a tool actually needs it."""

import asyncio
import contextlib
import errno
import hashlib
import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from functools import partial

import httpx
from sqlalchemy import exists, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import ConflictError, ForbiddenError, NotFoundError
from app.core.sandbox_auth import bind_resource_token, for_a_machine
from app.core.sentences import say
from app.domain.addressing import REASON_MACHINE_OWNER, Addressed, Recipient
from app.domain.agent import execution
from app.domain.agent.device_hub import DeviceCallError, DeviceOffline, device_hub
from app.domain.agent.device_provider import (
    device_home_dir,
    environment_status,
)
from app.domain.agent.environment import VM, Sandbox, SelfHosted, spec_of
from app.domain.agent.environment_failures import tell_failed
from app.domain.agent.harness.channel import ScreenSetupError, mint_session_token
from app.domain.agent.harness.claude_code import executor_launch as launch
from app.domain.agent.machine_address import device_api_base, site_forward, ws_url
from app.domain.agent.market import cloud_provisionable
from app.domain.agent.models import AgentTurn
from app.domain.agent.owner_provider import OWNER_CHANNEL
from app.domain.agent_instance.adapters import project_team
from app.domain.agent_instance.services import AgentInstanceService
from app.domain.agent_session.models import (
    LEFT_HOST_KEY,
    LOST_KEY,
    LOST_PROCESSES_KEY,
    THAW_KEY,
    AgentSession,
)
from app.domain.agent_session.services import AgentSessionService
from app.domain.conversation.services import of_room, room_of
from app.domain.delivery.ledger import deliver
from app.domain.delivery_events import DeliveryEvent, event_id_for
from app.domain.device import sandbox as device_sandbox
from app.domain.device.supply import Supply
from app.domain.device.wiring import sql_device_service
from app.domain.identity.actor import Actor
from app.domain.machine import holder as holders
from app.domain.machine import lease_claim
from app.domain.machine.environments import device_for_spec, environment_of
from app.domain.machine.infra_records import keep_refusal
from app.domain.machine.install_progress import InstallProgress, downloaded
from app.domain.machine.lease_claim import still_preparing
from app.domain.machine.lifecycle import (
    SandboxRemoving,
    frozen_sessions,
    thaw_if_asked,
    use_session_work,
)
from app.domain.machine.models import CloudHost
from app.domain.machine.progress import publish_line
from app.domain.machine.sandbox_wait import (
    DISK_FULL,
    DISK_FULL_NOTICE,
    EXECUTOR_SETUP_FAILED,
    SANDBOX_LOST,
    SANDBOX_PREPARING,
    SANDBOX_REPLACED,
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
from app.domain.room_task.models import Task
from app.domain.team.models import Team, TeamUserRelation
from app.domain.team.protocol import TeamMemberRole
from app.domain.topic.models import Topic, TopicKind
from app.domain.topic.services import TopicService
from app.domain.usage.compute import ComputeRefused
from app.domain.user.models import User
from app.domain.user.services import user_by_handle

logger = logging.getLogger(__name__)


def presentation(row):
    """A session and the machine it holds, for the pages that show it. A
    sandbox on one of the platform's machines does not say which one: that is
    the platform's scheduling and nobody's to read."""
    lease = row.work_lease
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


async def checkpoint(lease: dict, start: Callable[[], Awaitable[dict]]) -> bool:
    """One best-effort checkpoint on the machine a session is leaving.

    The same command a turn's Stop checkpoint runs there (``cheese sync
    --all``): every task checkout's unpushed commits go to its branch. What is
    not committed is not kept. Whatever happens — the machine is away, the
    command fails or outlasts ``PUSH_WAIT_S`` — the caller goes on: what a lost
    checkpoint loses is what was committed since the last turn's push. Answers
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
            lease = {**lease, "state": (await start())["state"]}
        result = await execution.call(
            lease,
            "control",
            {"subtype": "checkpoint", "request_id": f"leave-{uuid.uuid4()}"},
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
    rows = list(
        await session.scalars(
            select(AgentSession).where(
                of_room(AgentSession.conversation_id, topic_id),
                AgentSession.work_lease.is_not(None),
            )
        )
    )
    # Frozen: checkpointed before it was frozen, and nothing ran since.
    frozen = await frozen_sessions(session, [row.id for row in rows])
    starts = []
    for row in rows:
        if row.id in frozen:
            continue
        lease = row.work_lease or {}
        if lease.get("status", "ready") != "ready" or not lease.get("state"):
            continue
        try:
            starts.append((lease, await restart_executor(session, row, lease)))
        except Exception:  # best effort; the cleanup goes on
            logger.warning("no checkpoint for session %s", row.id, exc_info=True)
    # No transaction is held while the machines run it.
    await session.commit()
    await asyncio.gather(*(checkpoint(lease, start) for lease, start in starts))


def _executor_env(env, *, api, token, project_id, topic_id, author, work_resource):
    """What a session's executor runs with: the caller's ``CHEESE_*``/``GIT_*``
    values, then the platform's own for this session."""
    site = site_forward(api)
    return {
        **{
            key: value
            for key, value in env.items()
            if key.startswith(("CHEESE_", "GIT_"))
        },
        "CHEESE_API": api,
        # The machine's copy, marked as held there (`sandbox_auth.for_a_machine`).
        "CHEESE_TOKEN": for_a_machine(token) if token else "",
        # Empty for a sandbox in no project (``holder``).
        "CHEESE_PROJECT": str(project_id or ""),
        "CHEESE_TOPIC": str(topic_id),
        "CHEESE_AUTHOR": author,
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

    Every call into the machine is bounded by ``lease_claim.INSTALL_MOST_S``:
    a machine that stopped answering — its connector gone silent, its executor
    hung — fails this attempt instead of holding the tool call that asked for
    its hands. What the failure leaves behind is answered by the caller.

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
        if launch.can_prepare(running, sandbox):
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
        timeout=lease_claim.INSTALL_MOST_S,
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


async def restart_executor(db, row, lease):
    """How to start again the executor of the session ``row`` on the machine
    its ``lease`` is on, as a tool call there would: with the credential a
    session launches with, minted now, since the one it last ran with may have
    expired."""
    topic = await TopicService(db).get_or_404(await room_of(db, row.conversation_id))
    project = await ProjectService(db).get_or_404(topic.project_id)
    task = (
        await db.get(Task, row.conversation_id)
        if row.conversation_id != topic.id
        else None
    )
    chosen = await environment_of(db, row, topic, task, project)
    author = await _session_author(db, project, row.agent_handle)
    resource = lease.get("room_resource_id") or str(topic.resource_id or topic.id)
    token = bind_resource_token(
        mint_session_token(project.id, row.conversation_id, author),
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
        namespace=project.id,
        work_resource=work_resource,
        sandbox=await _sandboxed(chosen, db, lease["device_id"]),
        platform_machine=await _platform_machine(db, lease["device_id"]),
        setup=_executor_env(
            {"CHEESE_ENVIRONMENT": json.dumps(environment)},
            api=api,
            token=token,
            project_id=project.id,
            # The conversation the session works in, as at its install: a
            # task's session is told the task, not its channel.
            topic_id=row.conversation_id,
            author=author,
            work_resource=work_resource,
        ),
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
    the old one was on (``LEFT_HOST_KEY``)."""
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
    request = row.execution_request or {}
    old = row.work_lease
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
    if old and not await frozen_sessions(db, [session_id]):
        generation = request.get("generation")
        start = await restart_executor(db, row, old)
        await db.commit()
        await checkpoint(old, start)
        await TopicService(db).lock_for_execution(topic_id)
        row = await sessions.by_id(session_id, lock=True)
        if row is None:
            raise NotFoundError("Session not found")
        request = row.execution_request or {}
        old = row.work_lease
        if request.get("generation") != generation or not old:
            raise ConflictError(say("workComputerJustSwitched"))
        if still_preparing(old):
            raise ConflictError(say("machineAllocationInProgress"))
    on_platform = bool((old or {}).get("platform"))
    # The sandbox left behind is the idle sweep's, whether or not it was ever
    # handed to the session.
    left = await HostPool(db).leave(session_id)
    # A person's machine keeps the session's directories; the room's cleanup
    # finds them there by this lease.
    kept = [] if old is None or on_platform else [old]
    await sessions.set_environment(
        row.conversation_id,
        row.agent_handle,
        environment.model_dump(),
        chosen_by=asdict(actor),
    )
    request = {
        "generation": str(uuid.uuid4()),
        "retained_leases": [*request.get("retained_leases", []), *kept],
    }
    if left is not None:
        # The next sandbox is not put back on the host this one came from
        # without this (`HostPool._moved_off`): the host it just left is the
        # full one, and a full host is what placement prefers.
        request[LEFT_HOST_KEY] = str(left.id)
    row.execution_request = request
    row.work_lease = None
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


async def _verified(db, *, topic_id, session_id, claims, token, env, hub):
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
    unarchive), a sandbox given up with its host (``LOST_KEY``), a lease being
    re-checked or replaced (its ``verified_at`` is dropped, `_attempt`), a
    sandbox being destroyed or not touched for ``ACTIVE_STEP``, and a machine
    the hub does not reach are all left to the whole check."""
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
            ).where(AgentSession.id == session_id)
        )
    ).first()
    if found is None:
        return None
    row, agent_live = found
    lease = row.work_lease or {}
    request = row.execution_request or {}
    location = row.runtime_location or {}
    verified = lease.get("verified_at")
    if (
        not verified
        or datetime.now(UTC) - datetime.fromisoformat(verified) >= VERIFIED_FOR
        or lease.get("kind") != "device"
        or lease.get("status", "ready") != "ready"
        or not lease.get("generation")
        or lease["generation"] != request.get("generation")
        or lease.get("room_resource_id") != resource
        or lease.get("setup_digest") != _setup_digest(env)
        or request.get(LOST_KEY)
        or request.get(THAW_KEY)
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
    if lease.get("own") and not await HostPool(db, hub=hub).holds(
        session_id, lease["device_id"]
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
    is taken, and only its lease column read and written — the execution route
    calls this, and it is served by the device connection owner
    (`device/owner_reads.py` says why it names its columns)."""
    lease = await db.scalar(
        select(AgentSession.work_lease)
        .where(AgentSession.id == session_id)
        .with_for_update()
    )
    if lease and "verified_at" in lease and lease.get("generation") == generation:
        await db.execute(
            update(AgentSession)
            .where(AgentSession.id == session_id)
            .values(work_lease={k: v for k, v in lease.items() if k != "verified_at"})
        )
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
    session_id,
    claims,
    token,
    env,
    wait_s,
    gone,
    hub=None,
    tells_agent=False,
):
    """Hands for this session, waiting up to ``wait_s`` while they are prepared.

    ``gone`` reports that the caller has left (its command was stopped or
    timed out), which ends the wait without taking anything further.
    ``tells_agent``: the caller hands a ``notice`` in the answer to the agent
    beside its tool's result. Only such a caller is given one, and it is given
    once: a session whose sandbox was lost hears so on the first of its tool
    calls that has a new one, not on a hook's call that nobody reads.
    """
    hub = hub or device_hub
    held = await _verified(
        db,
        topic_id=topic_id,
        session_id=session_id,
        claims=claims,
        token=token,
        env=env,
        hub=hub,
    )
    if held is not None:
        return held
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
            tells_agent=tells_agent,
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


async def _attempt(
    db, *, topic_id, session_id, claims, token, env, hub, tells_agent=False
):
    """The row reservation survives worker death; remote work holds no DB lock."""
    holder = await holders.lock_for_work(db, topic_id)
    sessions = AgentSessionService(db)
    row = await sessions.by_id(session_id, lock=True)
    if row is None or await holders.holder_of(db, row.conversation_id) != topic_id:
        raise NotFoundError("Session not found")
    resource = holder.generation
    if (
        claims.get("session") != str(row.id)
        or claims.get("t") != str(row.conversation_id)
        or not holder.signed_for(claims)
    ):
        raise ForbiddenError("Execution credential does not own this session")
    request = row.execution_request or {"generation": str(uuid.uuid4())}
    environment = await holder.environment(db, row)
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
    location = row.runtime_location or {}
    # ponytail: 本地只有一台机器，会话主机与工作电脑必然是同一台；生产护栏
    # 「项目文件别和执行会话挤在一台机器上」在这里只能挡下整个演示（agent 的
    # Bash/文件工具全被 403，wolfram 技能脚本跑不起来），所以 development 下放行。
    # 上限：会话与项目工具共用一个连接器，资源不再隔离。
    # 升级路径：注册第二台机器当工作电脑后，或部署在非 development，这条不再触发。
    single_machine = settings.environment == "development"
    if (
        # A member's own agent may work on the machine its session runs on,
        # its owner's, when that is where its environment is: the session then
        # sees the project where the executor holds it (`owner_provider`).
        location.get("channel") != OWNER_CHANNEL
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
                partial(lease_claim.moved, db, session_id, lease.get("claim")),
            )
    devices = sql_device_service(db)
    if lease and await devices.get_device(lease["device_id"]) is None:
        # Its machine was unbound (a re-bind enrols a new device id): nothing on
        # it can be reached again.
        row.work_lease = lease = None
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
            agent_handle=row.agent_handle,
            spec=spec.name,
            project=await ProjectService(db).get_or_404(holder.project_id),
            task=await holders.task_of(db, holder, row),
            held=(lease or {}).get("device_id"),
            # A machine whose link dropped moments ago is called, and the call
            # waits for it (`_reachable`).
            is_online=partial(_reachable, hub),
        )
        if selected is None:
            await db.commit()
            return {"unavailable": str(say("specDevicesUnavailable"))}
    work_resource = (lease or {}).get("resource_id") or generation
    if selected is not None and isinstance(environment, Sandbox):
        try:
            await HostPool(db, hub=hub).keep_on_device(
                session_id,
                holder=holder,
                device_id=selected.device_id,
                spec=environment.spec,
                resource_id=work_resource,
            )
        except SandboxRemoving:
            # Its idle sandbox is being destroyed: the next attempt, once it is
            # gone, places the session in a new one.
            await db.commit()
            return _Preparing(SANDBOX_PREPARING, partial(_home_removed, db, session_id))
    cloud_host = None
    if selected is None and not cloud_provisionable(settings):
        await db.commit()
        return {"unavailable": str(say("platformSpecUnavailable"))}
    if selected is None:
        chosen = await sessions.environment(row.conversation_id, row.agent_handle)
        chooser = (chosen.chosen_by if chosen is not None else None) or None
        allocation_actor = (
            Actor(**chooser)
            if isinstance(chooser, dict)
            else Actor(handle=claims.get("a", ""), user_id=None, via="cheese")
        )
        pool = HostPool(db, hub=hub)
        whole_machine = spec is not None and spec.name == VM
        try:
            cloud_host = await pool.place(
                session_id,
                actor=allocation_actor,
                resource_id=work_resource,
                whole_machine=whole_machine,
            )
        except (CloudKeepsFailing, CloudPoolFull, ComputeRefused) as refused:
            if not isinstance(refused, ComputeRefused):
                await keep_refusal(
                    db,
                    session_id=session_id,
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
            # gone, places the session in a new one.
            await db.commit()
            return _Preparing(SANDBOX_PREPARING, partial(_home_removed, db, session_id))
        if not cloud_host.device_id or not hub.is_online(cloud_host.device_id):
            line = await pool.tell_waiting(session_id)
            await db.commit()
            await publish_line(topic_id, line)
            return _Preparing(
                VM_PREPARING if cloud_host.whole_machine else SANDBOX_PREPARING,
                partial(_cloud_progress, db, hub, cloud_host.id),
            )
        device_id = cloud_host.device_id
        # Provisioning releases its transaction around external calls.
        holder = await holders.lock_for_work(db, topic_id)
        row = await sessions.by_id(session_id, lock=True)
        if row is None or (row.execution_request or {}).get("generation") != generation:
            raise ConflictError("Execution request changed during provisioning")
    else:
        device_id = selected.device_id
    # Placement records the session host before the screen that asks opens.
    assert row.runtime_location is not None
    host = row.runtime_location["device_id"]
    if (
        device_id == host
        and row.runtime_location.get("channel") != OWNER_CHANNEL
        and not single_machine
    ):
        raise ForbiddenError("Project tools cannot execute on the session host")
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
                f"{host_api}/topics/{row.conversation_id}/execution/session-{resource}"
            ),
        }
    claim = str(uuid.uuid4())
    reservation = {
        "kind": "device",
        "session_id": str(session_id),
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
    row.work_lease = holding
    actor = await user_by_handle(db, claims.get("a", ""))
    if actor is None:
        raise ForbiddenError("Execution actor no longer exists")
    actor_handle = actor.username
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
            token=token,
            resource=resource,
            session_id=session_id,
            generation=generation,
            env=env,
            api=api,
            host_api=host_api,
            holder=holder,
            # The executor and its progress lines belong to the conversation;
            # its machine and lock to the holder.
            topic_id=row.conversation_id,
            actor_handle=actor_handle,
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
            tells_agent=tells_agent,
        )
    )
    _INSTALLS.add(work)
    work.add_done_callback(_installed)
    outcome = await asyncio.shield(work)
    if outcome is _CLOUD_PREPARING:
        assert cloud_host is not None
        return _Preparing(
            VM_PREPARING if cloud_host.whole_machine else SANDBOX_PREPARING,
            partial(_cloud_progress, db, hub, cloud_host.id),
        )
    return outcome


#: Installations a request started and may have stopped waiting for. Held
#: here so the event loop keeps them until they end.
_INSTALLS: set[asyncio.Future] = set()
#: What `_install` answers when a Cloud machine dropped during setup; the
#: request turns it into a wait on its own session.
_CLOUD_PREPARING = object()


def _installed(work: asyncio.Future) -> None:
    _INSTALLS.discard(work)
    if not work.cancelled() and work.exception() is not None:
        # Whoever asked may have stopped waiting; the claim has already lapsed
        # (`_install`), so the next attempt starts over.
        logger.warning("executor installation failed", exc_info=work.exception())


async def _install(
    bind,
    *,
    token,
    resource,
    session_id,
    generation,
    env,
    api,
    host_api,
    holder,
    topic_id,
    actor_handle,
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
    tells_agent=False,
):
    """Bring the session's executor up under ``claim`` and record the outcome."""
    async with AsyncSession(bind, expire_on_commit=False) as db:
        sessions = AgentSessionService(db)
        execution_token = holder.credential(
            token, session_id=str(session_id), lease_generation=generation
        )
        namespace = holder.namespace
        setup = _executor_env(
            env,
            api=api,
            token=holder.sandbox_token(execution_token),
            project_id=holder.project_id,
            topic_id=topic_id,
            author=actor_handle,
            work_resource=work_resource,
        )
        disk_full_mb = None
        # On a person's own computer the room sees the runtime download
        # (`install_progress`); a Cloud machine's getting ready is the
        # platform's own, kept out of the conversation (`progress`).
        shown = (
            InstallProgress(bind, session_id=session_id, device_id=device_id)
            if owner_device
            else contextlib.nullcontext()
        )
        try:
            async with lease_claim.kept(bind, session_id, claim, since=now):
                await thaw_if_asked(
                    db,
                    hub,
                    session_id=session_id,
                    device_id=device_id,
                    work_resource=work_resource,
                )
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
            row = await sessions.by_id(session_id, lock=True)
            if row and (row.work_lease or {}).get("claim") == claim:
                row.work_lease = {**holding, "claim_until": now.isoformat()}
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
                    session_id, cloud_host_id
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
        row = await sessions.by_id(session_id, lock=True)
        if (
            row is None
            or (row.work_lease or {}).get("claim") != claim
            or current.generation != resource
        ):
            raise ConflictError("Execution allocation changed while preparing")
        if target["status"] == "ready":
            # Checked end to end just now: what `_verified` hands out again.
            target["verified_at"] = datetime.now(UTC).isoformat()
            target["setup_digest"] = _setup_digest(env)
        row.work_lease = target
        lost = (
            tells_agent
            and target["status"] == "ready"
            and (row.execution_request or {}).get(LOST_KEY)
        )
        stopped = []
        if lost:
            request = row.execution_request or {}
            stopped = [str(one) for one in request.get(LOST_PROCESSES_KEY) or [] if one]
            row.execution_request = {
                key: value
                for key, value in request.items()
                if key not in (LOST_KEY, LOST_PROCESSES_KEY)
            }
        conversation_id = row.conversation_id
        device = (
            await sql_device_service(db).get_device(device_id) if owner_device else None
        )
        if device is not None:
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
            ready_line = await HostPool(db, hub=hub).tell_ready(session_id)
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
        lost_notice = SANDBOX_LOST if holder.project_id else SANDBOX_REPLACED
        notices = [lost_notice] * bool(lost)
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
        }


async def _another_attempt() -> bool:
    return True


# Freezing a sandbox runs this module's checkpoint first; `lifecycle` cannot
# import this module (it is imported by it), so it is handed the two it uses.
use_session_work(checkpoint=checkpoint, restart_executor=restart_executor)
