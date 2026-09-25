"""DeviceChannel — the self-hosted / BYO-compute channel (P3).

The session lives on a machine somebody enrolled rather than a platform
container. The platform opens a screen on it over the frozen ``link.Msg``
channel (``DeviceHub``), and the program that screen runs is the harness's
runner: it owns the agent process, journals what it says, and answers on a
socket the connector relays to (``hub.call_executor``).

Per request:
  1. resolve an online device bound to the project + its agent identity (DB),
  2. ensure a screen for the room on that device (open via ``DeviceHub`` if
     absent, or reuse the one whose runner still answers),
  3. let the device commit and push its own worktree back over git smart-HTTP.
"""

import asyncio
import hashlib
import inspect
import json
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from urllib.parse import urlparse

import httpx
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import settings
from app.core.sandbox_auth import (
    mint_scoped_token,
    scoped_token_claims,
    token_agent_handle,
)
from app.domain.agent import machine_launcher, provider_env
from app.domain.agent.device_hub import (
    DeviceCallError,
    DeviceHub,
    DeviceOffline,
    HubScreen,
    device_hub,
)
from app.domain.agent.harness import CLAUDE_CODE, SessionRef
from app.domain.agent.harness.channel import (
    SESSION_TOKEN_TTL_S,
    Channel,
    Placement,
    ScreenSetupError,
)
from app.domain.agent.harness.claude_code import (
    DEVICE_TUNNEL_PROBE,
    resident_release,
)
from app.domain.agent.harness.launch import ExecutorPlan, MachinePlace, MachinePlan
from app.domain.agent.place import (
    CHECKOUT_DIR,
    footprint_root,
    session_platform_dirs,
)
from app.domain.agent.platform_failures import (
    DEVICE_OFFLINE_MESSAGE,
    HOST_UNREACHABLE_CODE,
)
from app.domain.device.models import DeviceRow
from app.domain.device.service import DeviceService
from app.domain.device.supply import (
    Supply,
    has_runnable_transport,
)
from app.domain.device.wiring import sql_device_service
from app.domain.identity.services import IdentityService
from app.domain.topic.services import TopicService
from app.domain.user.services import user_by_handle

# Resolve the device a turn runs on for (project, topic) → (device_id, agent_user_id,
# agent_handle). Takes both ids because the device is chosen with topic affinity, not
# just per project (execution-architecture v4 §affinity).
logger = logging.getLogger(__name__)

# How long the launcher file write may go unanswered. The hub adds 5s of grace
# on top for the exec.result frame itself.
_LAUNCHER_SHIP_TIMEOUT_S = 30
_SESSION_RECONNECT_GRACE_S = 10.0
_SESSION_RECONNECT_POLL_S = 0.25

DeviceResolver = Callable[
    [uuid.UUID, uuid.UUID], Awaitable["tuple[str, int, str] | None"]
]


class EnvironmentPreparationError(ScreenSetupError):
    def __init__(self, status: dict):
        self.environment_status = status
        super().__init__(
            "环境准备失败，这条消息还没有开始处理。",
            failure_code="environment_preparation_failed",
        )


# #358 · what a turn gets when its only/pinned machine is enrolled as the boxed
# `isolated` 档: a clean, actionable refusal, NOT a silent bare-on-host launch. It
# is deliberately NOT one of platform_failures' host-scoped classifications — an
# `isolated` machine is not unhealthy, so this must never quarantine it; it is a
# 「该档尚未实现」 turn error the owner resolves by opting the machine into
# whole-machine (Hosted Machine), or by waiting for the sandbox transport (step 2).
DEVICE_ISOLATED_UNSUPPORTED_MESSAGE = (
    "话题与机器的绑定登记为『沙盒』档（visibility=isolated），但按房间隔离的容器传输"
    "尚未实现（#358 第二步）；平台拒绝以裸跑代替——那等于静默把整台机器暴露给这个"
    "房间。请把这台机器改登记为『整台机器（Hosted Machine）』后再继续。"
)

DEVICE_NOT_HOSTED_MESSAGE = (
    "话题当前绑定的是云端连接器，不是 Hosted 机器；Hosted 解析器拒绝把云端端点"
    "当作人的机器运行。"
)


async def resolve_pinned_device(
    service: DeviceService,
    is_online: Callable[[str], bool],
    project_id: uuid.UUID,
    topic_id: uuid.UUID,
) -> str | None:
    """The device this topic's turn must run on (execution-architecture v4 §affinity).

    A topic's work tree + resumable claude session live on ONE machine. So:
      * an existing binding — either a machine named before the first turn or the
        machine frozen by an earlier automatic choice — takes precedence over
        automatic selection. Return it **iff hosted, online, and runnable**; an
        offline binding raises
        (queue/retry) and an `isolated` binding raises the #358 「尚未实现」 error.
        NEVER fall back to another device, which would break an explicit choice or
        start a resumed topic from an empty tree;
      * no binding means 「系统挑一台」 on the first turn: pick the first online,
        **non-quarantined** hosted device serving the project and create a runnable
        ``host`` binding (write-once), so every later turn returns to it. Quarantined
        = judged unhealthy by ``device.health``; a topic that is already bound
        is never moved — not here, not anywhere (``agent.host_failure``).

    The #358 visibility gate lives entirely here (the one resolution point every
    production turn passes through), so an `isolated` device — whose per-room
    container transport is #358 step 2 — is never pinned to a topic nor launched
    bare-on-host in its place: silently degrading `isolated` to bare is exactly the
    whole-machine exposure the gate exists to prevent.

    Returns the device id, or ``None`` when no runnable bound device is online at all
    (the caller turns that into a clean "no online device" turn error)."""
    # The compute-profile route records a named machine by writing this binding
    # before the first turn. Consequently only a NULL binding means the user chose
    # 「系统挑一台」; do not consult the healthy-device pool when a binding exists.
    chosen = await service.topic_binding(topic_id)
    if chosen is not None:
        device_id = chosen.device_id
        if not await service.serves_project(device_id, project_id):
            raise ScreenSetupError("设备已移出团队或项目，请联系设备所有者")
        if await service.get_hosted_device(device_id) is None:
            raise ScreenSetupError(DEVICE_NOT_HOSTED_MESSAGE)
        if not is_online(device_id):
            raise ScreenSetupError(
                DEVICE_OFFLINE_MESSAGE, failure_code=HOST_UNREACHABLE_CODE
            )
        # An isolated binding must refuse rather than run bare — the pin does not
        # move, but the turn will not silently expose the whole machine either.
        if not has_runnable_transport(chosen.visibility):
            raise ScreenSetupError(DEVICE_ISOLATED_UNSUPPORTED_MESSAGE)
        return device_id
    # 「系统挑一台」 on the first turn: pick from machines that are online AND not
    # quarantined. A quarantined machine just failed two turns in a row for a reason
    # that belongs to the box (#186), so pinning a fresh topic to it would hand the
    # next person the failure we already diagnosed. Note this filter applies to the
    # FIRST pin only. This resolver never moves an ALREADY-pinned topic — nothing
    # does; a machine judged dead is named in the room (``agent.host_failure``)
    # and waited for, because a pin that can quietly change is the original drift
    # bug.
    device = await service.first_healthy_device(project_id, is_online)
    if device is None:
        return None
    # The same fact the market catalogue publishes as `default=True`, read from
    # one place so the picker can never advertise a 档 the resolver does not
    # bind. Today that resolves to `host`, because `isolated` has no transport;
    # when #358 step 2 supplies one, this and the catalogue move together.
    await service.bind_topic_device(
        topic_id,
        device.device_id,
        visibility=await service.binding_visibility(device.device_id),
    )
    return device.device_id


# Addresses that only mean something ON the box. Routing the box's own turns
# through the local LLM gateway / metering proxy is what makes their spend
# visible — but the same value handed to a machine somewhere else names nothing
# there, and the failure is a turn that dies on a connection error with no hint
# why.
_BOX_LOCAL_HOSTS = ("localhost", "127.0.0.1", "172.17.0.1", "172.18.0.1", "litellm")


def uses_tunnel(*, tunnel_url: str) -> bool:
    """Whether this screen's CONNECT traffic rides the tunnel.

    Device execution never assumes access to the backend host's private network.
    When a tunnel is configured every device uses it; otherwise the device dials
    ``subscription_device_proxy_host`` as it does today. That is right for a flat
    network and wrong for this one, which is exactly why it is a setting rather
    than a guess: the deployment knows whether its machines can reach the box.
    """
    return bool(tunnel_url.strip())


async def device_api_base(session, device_id: str, public_base: str) -> str:
    """The backend base that ``device_id`` dials, from configuration.

    An address belongs to the dialer: the session host reaches the backend over
    its own configured base, a private-control cloud machine over loopback, and
    everything else over the public connector base.
    """
    if (
        device_id == settings.agent_session_device_id
        and settings.agent_session_api_base
    ):
        return settings.agent_session_api_base.rstrip("/")
    device = await session.get(DeviceRow, device_id)
    if device and device.supply == Supply.cloud and device.cloud_control_private:
        return "http://127.0.0.1:18080"
    return public_base.rstrip("/")


def _preview_ws_url(public_base: str) -> str:
    """``wss://…/preview/tunnel`` for a machine, from the base it already dials.

    Scheme-swapped rather than configured: the connector and the CLI all reach
    this origin already, so a preview that rides the same one needs no
    second address to keep true — and a deployment cannot end up with a preview
    pointed somewhere the machine was never able to reach.
    """
    base = public_base.rstrip("/")
    for http_scheme, ws_scheme in (("https://", "wss://"), ("http://", "ws://")):
        if base.startswith(http_scheme):
            base = ws_scheme + base[len(http_scheme) :]
            break
    return f"{base}/preview/tunnel"


def connect_transport(*, session_token: str, via_tunnel: bool) -> str | None:
    """The ``HTTPS_PROXY`` value that steers this screen to the meter, or None
    when the backend does not know it.

    Through the tunnel it does not: the address is the helper's loopback port,
    and that port is the machine's to choose — the launcher asks the kernel for
    a free one and exports it itself. It carries NO credential either way: the
    helper reads the scoped token from a file the launcher writes, so a refreshed
    token takes effect without relaunching `claude`, which reads HTTPS_PROXY
    exactly once at startup (#385).

    Direct, the scoped token rides as the proxy password, which is what stops an
    exposed listener relaying for anyone who cannot prove which project to bill.
    """
    if via_tunnel:
        return None
    host = (
        settings.subscription_device_proxy_host.strip()
        or settings.subscription_proxy_host
    )
    return (
        f"http://cheese:{session_token}@{host}:"
        f"{settings.subscription_proxy_connect_port}"
    )


# Where the launch script writes the metering proxy's CA on the device (under the
# screen's ISOLATED home) and exports NODE_EXTRA_CA_CERTS to point. The env value
# built here carries the literal placeholder; only the script knows the real home.
# The `.claude` in it is the launcher's config dir, so this string and
# `device_launch` have to agree — it is written down twice today, once on each
# side of the seam.
_DEVICE_PROXY_CA_PATH = "$HOME/.claude/proxy-ca.pem"


def _launch_identity(
    *,
    agent_configuration: str,
    harness_contract: str,
    execution_target: dict | None,
) -> str:
    """What a live session is compared against to decide it still matches what
    the backend would start today.

    Everything a running process cannot adopt without being restarted, in one
    value: the model and role it was born with, the harness's whole launch
    (``MachineLaunch.contract``), the platform's half of the launcher, the
    executor it was handed, and the directory the platform installed itself
    into. Anything left out is a change that lands in the code and never
    reaches the rooms already running — a pinned harness version once moved
    while every reused screen kept the one it started with, and later a new
    shell prefix reached no room that was already open.
    """
    return hashlib.sha256(
        json.dumps(
            {
                "agent": agent_configuration,
                "harness": harness_contract,
                # The platform half has no per-room content of its own: that
                # arrives as environment, so with empty holes it is the same
                # script for every room.
                "launcher": hashlib.sha256(
                    machine_launcher.launch_script(command="").encode()
                ).hexdigest(),
                "target": execution_target,
                "root": footprint_root(),
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()


def _warn_if_model_endpoint_is_box_local(env: dict[str, str], device_id: str) -> None:
    # HTTPS_PROXY is the machine's CONNECT route to the metering proxy, and the
    # only way its traffic reaches a model at all. Pointing it at a box-local
    # address fails off-box.
    value = env.get("HTTPS_PROXY", "")
    if any(h in value for h in _BOX_LOCAL_HOSTS):
        logger.error(
            "device %s received HTTPS_PROXY=%s, which only "
            "resolves on the backend's own host — its turns will fail to "
            "reach a model. Give devices a reachable address "
            "(subscription_device_proxy_host for the metering proxy) or "
            "configure subscription_tunnel_url.",
            device_id,
            value,
        )


def _read_proxy_ca() -> str:
    """The metering proxy's CA, read where THIS backend can see it — required for
    every device turn (the launcher embeds it; without it the screen's `claude`
    cannot trust the proxy and fails as an opaque TLS error). Raising here, with
    the setting named, is the only exit: there is no second launch shape to fall
    back to, and falling back to one would swap the model out from under the
    user — the exact failure #325 G2 removes."""
    path = settings.subscription_ca_backend_path.strip()
    if not path:
        raise ScreenSetupError(
            "未设置 SUBSCRIPTION_CA_BACKEND_PATH——device 屏幕需要后端能读到"
            "计费代理的 CA（部署侧把代理的 mitmproxy-ca-cert.pem 只读挂载进"
            "后端并指向它）"
        )
    try:
        ca = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise ScreenSetupError(
            f"读取计费代理 CA 失败（SUBSCRIPTION_CA_BACKEND_PATH={path}）：{exc}"
        ) from exc
    if not ca.strip():
        raise ScreenSetupError(
            f"计费代理 CA 为空（SUBSCRIPTION_CA_BACKEND_PATH={path}）"
        )
    return ca


# How long a reuse check may wait on the machine (the runner's ping, the tunnel
# probe). Short by design, and a timeout is read as healthy, never as death: a
# probe hiccup must not throw away a session and its work.
_ALIVE_PROBE_TIMEOUT_S = 8.0

# Retire-and-reopen a reused screen whose baked credential is within this many
# seconds of expiry, before reusing a running device session
# so the backend's reuse decision and the on-device create gate agree on ONE
# margin. Small on purpose: it only rejects an already-dead-or-dying credential,
# never a healthy one, so a short-lived token (the gateway path's hour) is
# re-minted at most once per margin rather than on every turn.
_CREDENTIAL_EXPIRY_MARGIN_S = 300


def _credential_expiry(token: str) -> int:
    """Read the credential's birth expiry, retained with its device session.

    A development token without an expiry gets the normal session lifetime.
    """
    claims = scoped_token_claims(token)
    exp = claims.get("exp") if claims else None
    if isinstance(exp, int):
        return exp
    return int(time.time()) + SESSION_TOKEN_TTL_S


# The platform's footprint on the device, and a place's isolated claude home
# inside it, relative to the device's own `$HOME` (expanded by its shell, never
# by us). Everything below hangs off `DEVICE_ROOT` so that the launcher that
# creates these, the retirement that removes them (topic/retire.py) and the
# connector's `uninstall` are all naming one directory.
DEVICE_ROOT = f"$HOME/{footprint_root()}"
DEVICE_HOME_ROOT = f"{DEVICE_ROOT}/home"
DEVICE_WORK_ROOT = f"{DEVICE_ROOT}/work"
# Where a PROJECT's rooms share the packages they install, on the machine they
# share. Per project rather than per room because every room of a project
# installs the same lockfile, while a room's home is its own — and uv and pnpm
# both default their store inside `$HOME`, so N rooms meant N physical copies of
# one dependency tree. That is not a prediction: it is the failure already
# measured on the container path this replaces, where one project's 220
# worktrees came to hold 236GB.
#
# Per project rather than per MACHINE for the boundary it draws, not for the
# saving it gives up. Rooms of one project already share a repository and can
# read each other's checkouts, so a store inside that line reaches nothing that
# was not already reachable; a machine-wide store would cross a line that
# exists. Today no device room is isolated from any other either (supply.py:
# every screen is `host`), so this widens nothing at all — it is drawn per
# project for #358's sake, not for today's.
#
# What #358 will have to solve rather than inherit: hardlinks cannot cross a
# bind mount (link(2) → EXDEV even within one filesystem — verified on the dev
# box, where a cross-mount `ln` inside a sandbox failed with "Invalid
# cross-device link" and uv/pnpm silently fell back to full copies). So binding
# this store into an isolated room read-only would give that room the isolation
# and take the dedup straight back. The two levers are not the same lever, and
# a design that assumes they are will re-discover 236GB.
DEVICE_STORE_ROOT = f"{DEVICE_ROOT}/store"


def device_home_dir(project_id: uuid.UUID, place_id: uuid.UUID) -> str:
    return f"{DEVICE_HOME_ROOT}/{project_id}/{place_id}"


def device_work_dir(project_id: uuid.UUID, place_id: uuid.UUID) -> str:
    return f"{DEVICE_WORK_ROOT}/{project_id}/{place_id}"


def device_store_dir(project_id: uuid.UUID) -> str:
    return f"{DEVICE_STORE_ROOT}/{project_id}"


def launcher_path(topic_id: uuid.UUID) -> str:
    """The launcher file a screen runs, where `_ship_launcher` writes it."""
    return f"{DEVICE_ROOT}/launch/{topic_id}.sh"


# Where a place's environment runner may have been left, relative to that
# place's home, in precedence order. Every root the platform has ever installed
# into belongs here, because a place prepared under an earlier one keeps the
# runner where that launcher put it until something relaunches it — and the
# status it prepared is real the whole time. Each copy is byte-identical and all
# of them keep their state in the same `$HOME/.cheese-environment/status.json`,
# so whichever one we find answers for the place. Probing only the current root
# is what made a place prepared by another launcher read as `pending` forever:
# the ready status was on disk, one directory over. Which directories those are
# is `place.session_platform_dirs()`, so one the platform adds or drops reaches
# the probe by itself — see test_environment_status_probe.py. The `$HOME` below
# is the place's own: the command that reads these exports it first.
ENVIRONMENT_RUNNER_PATHS = tuple(
    f"$HOME/{directory}/cheese-environment.py" for directory in session_platform_dirs()
)


async def environment_status(
    hub: DeviceHub,
    device_id: str,
    project_id: uuid.UUID,
    topic_id: uuid.UUID,
    *,
    action: str = "status",
    wait_ready: bool = False,
) -> dict:
    home = device_home_dir(project_id, topic_id)
    # Inside the place's home, like everything else in this command: it runs
    # after the `export HOME` below, so `$HOME` here is `home` and not the
    # machine's own. Spelling it `DEVICE_ROOT` would read as the machine root
    # and land in the same place anyway, which is the kind of agreement that
    # survives until someone believes it.
    place_root = session_platform_dirs()[0]
    reset_marker = (
        f'mkdir -p "$HOME/{place_root}"; '
        f'touch "$HOME/{place_root}/environment-restart"; '
        if action == "reset"
        else ""
    )
    candidates = " ".join(f'"{path}"' for path in ENVIRONMENT_RUNNER_PATHS)
    result = await hub.exec(
        device_id,
        [
            "sh",
            "-c",
            f'export HOME="{home}"; '
            f"for candidate in {candidates}; do "
            'if [ -f "$candidate" ]; then '
            f"CHEESE_STATUS_WAIT={int(wait_ready)} "
            f'python3 "$candidate" {action} || exit $?; '
            "CHEESE_ENVIRONMENT_RAN=1; break; fi; done; "
            'if [ -z "$CHEESE_ENVIRONMENT_RAN" ]; then '
            "printf '%s' '{\"state\":\"pending\"}'; fi; " + reset_marker,
        ],
        timeout=10,
    )
    if result.get("exit") != 0:
        raise ScreenSetupError("无法读取机器上的环境准备状态")
    return json.loads(result.get("stdout") or '{"state":"pending"}')


def _launcher_command(topic_id: uuid.UUID) -> list[str]:
    """What a screen runs: the launcher file `_ship_launcher` wrote for this topic."""
    return ["bash", "-lc", f'exec bash "{launcher_path(topic_id)}"']


class DeviceChannel(Channel):
    """The REMOTE channel: a screen on a user's enrolled machine, opened over
    the frozen link.Msg link (DeviceHub). The screen is a ``HubScreen``.

    Enrollment, device resolution and the launcher shipped to the machine are
    what this file is about; what runs on the screen is the harness's runner."""

    name = "device"
    # 人接入的机器：平台只能停止使用它，不能销毁它。「谁开的」是一个常量，不从机器
    # 长什么样推 (#282 决定 2)。基类的 `owns` 读的就是这一位。
    supply = Supply.self_hosted
    # ``_ensure_screen`` below builds the whole model environment on the machine
    # — one shape, the metering-proxy env, and no other. The machine holds no
    # provider credential and no model name; both are settled per request at
    # admission. Every transport that reaches a machine over a link inherits
    # this build, and inherits the declaration with it.
    builds_model_env = True
    # 要手的一轮要不到手时说的那一句。供给不同，这一句不同，而「要不要手、要不到
    # 就停」那条分支三种供给是同一条——所以变的是这一句，不是那条分支。
    no_machine_message = "绑定的设备不在线，本轮无法运行"

    def __init__(
        self,
        *,
        hub: DeviceHub | None = None,
        session_factory: async_sessionmaker | None = None,
        device_resolver: DeviceResolver | None = None,
        public_base: str | None = None,
    ) -> None:
        self._hub = hub or device_hub
        self._session_factory = session_factory
        # A resolver may be injected (tests / future routing); otherwise the DB-backed
        # resolver is used lazily (keeps this module importable without a DB).
        self._device_resolver = device_resolver
        self._public_base = (public_base or settings.connector_public_base).rstrip("/")

    def available(self) -> bool:
        """Whether any device is currently connected (online). Project-level checks
        happen per turn, in ``precheck``."""
        return bool(self._hub.online_device_ids())

    async def restore_screens(
        self, scopes: list[tuple[uuid.UUID, uuid.UUID, str]]
    ) -> list[tuple[uuid.UUID, uuid.UUID, object | None, str | None]]:
        """Rebuild screen identities for the rooms this channel owns in the DB."""
        inventories = {}
        for device_id in {scope[2] for scope in scopes}:
            try:
                inventories[device_id] = await self._hub.list_screens(device_id)
            except (DeviceOffline, DeviceCallError, TimeoutError) as exc:
                logger.warning(
                    "Screen recovery failed for device %s: %s", device_id, exc
                )
                inventories[device_id] = []
        if not any(inventories.values()):
            return [(project, topic, None, None) for project, topic, _ in scopes]
        factory = self._session_factory
        if factory is None:
            from app.core.db import async_session_factory

            factory = async_session_factory
        restored = []
        # What the database knows, gathered first and committed, so that the
        # adoptions below — a call to the connection owner each when it runs as
        # its own service — run with no transaction open. One transaction across
        # every room of a reconnecting device kept a pool connection for as long
        # as the whole device took (dev, 2026-09-19).
        rooms: list[
            tuple[uuid.UUID, uuid.UUID, str, uuid.UUID | None, list, tuple]
        ] = []
        async with factory() as session:
            for project_id, topic_id, device_id in scopes:
                room = await TopicService(session).get(topic_id)
                current_resource = (room.resource_id or topic_id) if room else None
                entries = [
                    entry
                    for entry in inventories[device_id]
                    if (
                        entry.get("env", {}).get("CHEESE_PROJECT"),
                        entry.get("env", {}).get("CHEESE_TOPIC"),
                    )
                    == (str(project_id), str(topic_id))
                ]
                identity: tuple = ()
                if entries:
                    agent = await IdentityService(session).ensure_room_agent_user(
                        topic_id
                    )
                    identity = (agent.id, agent.username)
                rooms.append(
                    (
                        project_id,
                        topic_id,
                        device_id,
                        current_resource,
                        entries,
                        identity,
                    )
                )
            await session.commit()
        for (
            project_id,
            topic_id,
            device_id,
            current_resource,
            entries,
            identity,
        ) in rooms:
            screen = None
            for entry in entries:
                env = entry.get("env", {})
                expiry = env.get("CHEESE_TOKEN_EXPIRES")
                target = env.get("CHEESE_EXECUTION_TARGET")
                recovered = self._hub.adopt_screen(
                    device_id,
                    entry["sid"],
                    token=entry["screen"],
                    agent_user_id=identity[0],
                    agent_handle=identity[1],
                    project_id=project_id,
                    topic_id=topic_id,
                    resource_id=uuid.UUID(
                        env.get("CHEESE_RESOURCE_ID") or str(topic_id)
                    ),
                    command=entry["command"],
                    credential_expires=int(expiry) if expiry else None,
                    execution_target=json.loads(target) if target else None,
                    agent_configuration=env.get("CHEESE_AGENT_CONFIG", ""),
                )
                if inspect.isawaitable(recovered):
                    recovered = await recovered
                # Retired generations remain registered for durable cleanup;
                # only the room's current generation can resume its turn.
                if recovered.resource_id == current_resource:
                    screen = recovered
            restored.append((project_id, topic_id, screen, None))
        return restored

    # --- device / screen resolution ----------------------------------------

    async def _resolve_device_agent(
        self, project_id: uuid.UUID, topic_id: uuid.UUID
    ) -> tuple[str, int, str] | None:
        """The device this topic's turn runs on + its agent identity →
        ``(device_id, agent_user_id, agent_handle)``, or ``None`` when no bound device
        is online. Raises ``ScreenSetupError`` when the topic's *pinned* device is
        offline (queue, don't drift — v4 §affinity).

        Device pick has **topic affinity** (``resolve_pinned_device``): a topic freezes
        to the device its first turn ran on and every later turn returns to it — never
        drifts to another online device (which would lose the work tree / break resume).

        The device is PURE COMPUTE (execution-architecture v3: AIPool ⊥ ComputePool) —
        it carries no agent identity. The agent a screen runs as is the *project's*
        agent, resolved independently of the host machine (fusion-design §5: agent =
        screen, not machine). The agent is THIS topic's 分身 — its own agent-user
        (``cheese-<topic hex>``), the SAME identity the local path authors and mints
        tokens as — so a turn's author is identical whether it runs locally or on a
        self-hosted box, and is attributable to one 分身 either way. The device stays
        pure compute."""
        if self._device_resolver is not None:
            return await self._device_resolver(project_id, topic_id)
        factory = self._session_factory
        if factory is None:
            from app.core.db import async_session_factory

            factory = async_session_factory
        async with factory() as session:
            service = sql_device_service(session)
            device_id = await resolve_pinned_device(
                service, self._hub.is_online, project_id, topic_id
            )
            if device_id is None:
                return None
            # The screen acts as the agent that answers this room (its own
            # agent-user), so a turn run on a self-hosted box is attributable to the
            # same identity as one run locally — the device stays pure compute
            # either way.
            agent = await IdentityService(session).ensure_room_agent_user(topic_id)
            # Persist the pin created above (first turn) before the turn proceeds, so a
            # concurrent/next turn sees the same device.
            await session.commit()
            return device_id, agent.id, agent.username

    async def _retire_screen(
        self, screen: HubScreen, *, topic_id: uuid.UUID, reason: str
    ) -> None:
        """Close a reused screen, and say which gate decided to.

        Retiring one ends the `claude` behind it, so the turn that asked for the
        screen is answered by a process seconds old — which a room experiences as
        its platform tools briefly not existing, and a measurement experiences as a
        turn that is several seconds slower than the one before it for no reason
        visible anywhere. Which of the gates above fired was in no log: an
        occurrence could only be reconstructed afterwards from the connector's
        access log, which records the close but not the why, and not at all once
        the backend that decided had been replaced by a release.
        """
        logger.info(
            "device_screen_retired topic=%s device=%s sid=%s reason=%s",
            topic_id,
            screen.device_id,
            screen.sid,
            reason,
        )
        await self._hub.close_screen(screen.device_id, screen.sid)

    def _existing_screen(
        self, device_id: str, topic_id: uuid.UUID, resource_id: uuid.UUID | None = None
    ) -> HubScreen | None:
        for screen in self._hub.all_online_screens():
            if (
                screen.device_id == device_id
                and screen.topic_id == topic_id
                and (
                    resource_id is None
                    or (screen.resource_id or topic_id) == resource_id
                )
            ):
                return screen
        return None

    async def _device_api_base(self, device_id: str) -> str:
        factory = self._session_factory
        if factory is None:
            from app.core.db import async_session_factory

            factory = async_session_factory
        async with factory() as session:
            return await device_api_base(session, device_id, self._public_base)

    def _work_dir(self, project_id: uuid.UUID, topic_id: uuid.UUID) -> str:
        """The screen's cwd on the device.

        Every device owns an independent checkout. Physical host placement never
        changes this boundary: files cross it through git, never by translating a
        backend path into the device's namespace.
        """
        return f"{device_home_dir(project_id, topic_id)}/{CHECKOUT_DIR}"

    def _no_proxy_hosts(self) -> str:
        """What the screen's HTTPS_PROXY must NOT capture: the backend itself
        (git smart-HTTP, the `cheese` CLI) and loopback (local MCP). The
        CLI sends even plain-http requests through HTTPS_PROXY — measured — so
        without this the platform wiring detours through the meter, or dies with
        it when the meter is unreachable."""
        hosts = ["localhost", "127.0.0.1", "::1"]
        backend = urlparse(self._public_base).hostname
        if backend and backend not in hosts:
            hosts.insert(0, backend)
        return ",".join(hosts)

    async def _ship_launcher(
        self,
        device_id: str,
        topic_id: uuid.UUID,
        command: list[str],
        home_dir: str,
        release_state: dict | None = None,
        execution_token: str | None = None,
    ) -> list[str]:
        """Write the launch script to a FILE on the device (over the link's one-shot
        ``exec``, script on stdin) and return a short command that runs it.

        The launcher cannot ride in argv: the frozen cli hands the command to
        ``tmux new-session``, and tmux's client→server imsg buffer caps the whole
        packed command around 16KB — beyond it new-session dies with ``command too
        long``. Since #308 embedded the assembled system prompt in the script, every
        real launch is tens of KB, so argv delivery broke every device spawn (and
        the failure was invisible: session.error is fire-and-forget and the prompt
        just timed out). The file path is per-topic and written whenever a screen
        is created; a live screen is refreshed by ``_refresh_screen_files``
        instead, since it never runs its launcher again."""
        assert command[:2] == ["bash", "-lc"] and len(command) == 3
        script = command[2]
        path = launcher_path(topic_id)
        transfer, exec_env = self._screen_file_refresh(
            home_dir, release_state=release_state, execution_token=execution_token
        )
        transfer = f'mkdir -p "{DEVICE_ROOT}/launch" && cat > "{path}" && ' + transfer
        started = time.monotonic()
        try:
            result = await self._hub.exec(
                device_id,
                ["sh", "-c", transfer],
                stdin=script,
                env=exec_env,
                timeout=_LAUNCHER_SHIP_TIMEOUT_S,
            )
        except (TimeoutError, DeviceOffline) as exc:
            # The first thing a turn asks a machine to do, and on a freshly
            # enrolled Cloud box the first frame its connector ever has to
            # answer. Measured 2026-08-29 (machine 477): this exec got no answer
            # and the room read 「device 后端启动失败：TimeoutError」 — no step, no
            # machine, no word on whether the connector was even connected.
            raise ScreenSetupError(
                self._link_failure(
                    device_id,
                    step="写启动脚本",
                    waited_s=time.monotonic() - started,
                    offline=isinstance(exc, DeviceOffline),
                )
            ) from exc
        logger.info(
            "launcher shipped to device %s in %.3fms (topic=%s, %d bytes)",
            device_id,
            (time.monotonic() - started) * 1000,
            topic_id,
            len(script),
        )
        if result.get("exit") != 0:
            raise ScreenSetupError(
                f"无法把启动脚本写到设备上：{result.get('stderr') or result}"
            )
        if release_state is not None:
            release_state["version"] = result.get("stdout", "").strip()
        return _launcher_command(topic_id)

    @staticmethod
    def _screen_file_refresh(
        home_dir: str,
        *,
        release_state: dict | None,
        execution_token: str | None,
    ) -> tuple[str, dict[str, str] | None]:
        """The shell that brings a screen's per-turn files up to date: the
        forwarded-fs token (rotated every turn), and a read of the release
        marker when the caller tracks one. Shared by the launcher ship and the
        live-screen refresh, so both paths write the same files the same way."""
        hook_dir = f"{home_dir}/{session_platform_dirs()[0]}"
        transfer = f'mkdir -p "{hook_dir}"'
        if release_state is not None:
            transfer += (
                f' && if [ -f "{hook_dir}/remote-execution/release-ready" ]; then '
                f'cat "{hook_dir}/remote-execution/release-ready"; fi'
            )
        exec_env = None
        if execution_token is not None:
            token_path = f"{hook_dir}/remote-session/execution.token"
            transfer += (
                f' && mkdir -p "{hook_dir}/remote-session"'
                f' && umask 077 && printf %s "$CHEESE_FORWARDED_TOKEN"'
                f' > "{token_path}.next.$$"'
                f' && mv "{token_path}.next.$$" "{token_path}"'
            )
            exec_env = {"CHEESE_FORWARDED_TOKEN": execution_token}
        return transfer, exec_env

    async def _refresh_screen_files(
        self,
        device_id: str,
        home_dir: str,
        release_state: dict | None = None,
        execution_token: str | None = None,
    ) -> None:
        """A live screen keeps the session it was born with and never runs its
        launcher again, so a reused turn ships only what that process will
        actually read next: the token and the release marker. The launcher
        itself is 450 KB of embedded helper sources plus the prompt, and
        sending it here bought nothing but 46 ms on every turn."""
        transfer, exec_env = self._screen_file_refresh(
            home_dir, release_state=release_state, execution_token=execution_token
        )
        try:
            result = await self._hub.exec(
                device_id,
                ["sh", "-c", transfer],
                env=exec_env,
                timeout=_LAUNCHER_SHIP_TIMEOUT_S,
            )
        except (TimeoutError, DeviceOffline) as exc:
            raise ScreenSetupError(
                self._link_failure(
                    device_id,
                    step="刷新会话文件",
                    waited_s=_LAUNCHER_SHIP_TIMEOUT_S,
                    offline=isinstance(exc, DeviceOffline),
                )
            ) from exc
        if result.get("exit") != 0:
            raise ScreenSetupError(
                f"无法刷新设备上的会话文件：{result.get('stderr') or result}"
            )
        if release_state is not None:
            release_state["version"] = result.get("stdout", "").strip()

    @staticmethod
    def _resident_release_due(state: dict) -> bool:
        """Whether the screen's release marker is behind this backend's release."""
        return state.get("version") != resident_release.digest(
            resident_release.sources()
        )

    async def _runner(
        self, device_id: str, state: str, method: str, params: dict | None = None
    ) -> dict | None:
        """Ask a screen's runner something. None means there is no runner there.

        Only the machine saying so counts as none: a refused socket is a
        session that is gone (or a screen from before runners, whose process
        no call can reach). A timeout or a connection lost on the way says
        nothing about the session, so it is read as there, and the caller
        leaves a session that may be working alone.
        """
        try:
            return await self._hub.call_executor(
                device_id,
                state,
                method,
                params or {},
                timeout=_ALIVE_PROBE_TIMEOUT_S if method == "ping" else 660,
            )
        except DeviceCallError:
            return None
        except (TimeoutError, httpx.TransportError):
            return {"alive": True, "unknown": True}

    async def _refresh_resident(
        self, screen: HubScreen, home_dir: str, state: str, release: dict
    ) -> bool:
        """Put a new release of the remote-execution helpers into a live session.

        The helpers are replaced on disk and the running session is told to
        reload: `/reload-plugins` for the plugin that routes its tools to the
        executor, and a reconnect of the MCP server that carries them. The
        session keeps its conversation throughout.
        """
        sources = resident_release.sources()
        version = resident_release.digest(sources)
        if release.get("version") == version:
            return False

        async def execute(function, *args):
            result = await self._hub.exec(
                screen.device_id,
                ["python3", "-"],
                stdin=resident_release.script(function, *args),
                timeout=40,
            )
            if result.get("exit") != 0:
                raise ScreenSetupError(
                    f"Resident release {function} failed: {result.get('stderr')}"
                )
            return json.loads(result["stdout"])

        staged = await execute("stage", home_dir, sources)
        if staged.get("busy"):
            # Helpers are not replaced under a conversation that is still
            # running. This turn runs on the release it has; the marker stays
            # behind, so the next turn tries again.
            logger.info(
                "resident release deferred topic=%s reason=conversation_busy",
                screen.topic_id,
            )
            return False
        if not staged["changed"]:
            return False
        await self._command(screen.device_id, state, "/reload-plugins")
        await self._control(screen.device_id, state, "mcp_reconnect")
        await self._await_native_connected(screen.device_id, state)
        await execute("acknowledge", home_dir, version)
        logger.info(
            "resident release applied topic=%s version=%s", screen.topic_id, version
        )
        return True

    async def _command(self, device_id: str, state: str, text: str) -> None:
        answer = await self._runner(device_id, state, "command", {"text": text})
        if answer is None or answer.get("is_error") or "unknown" in answer:
            raise ScreenSetupError(f"The session did not complete {text}")

    async def _control(self, device_id: str, state: str, subtype: str) -> dict:
        answer = await self._runner(
            device_id,
            state,
            "control",
            {"request": {"subtype": subtype, "serverName": "native"}},
        )
        if not answer or answer.get("subtype") != "success":
            raise ScreenSetupError(f"Resident MCP {subtype} did not complete")
        return answer.get("response", {})

    @staticmethod
    def _native_status(status: dict) -> str:
        return next(
            (
                server.get("status", "")
                for server in status.get("mcpServers", [])
                if server.get("name") == "native"
            ),
            "",
        )

    async def _await_native_connected(
        self, device_id: str, state: str, timeout_s: float = 30
    ) -> None:
        """A reconnect is acknowledged while the server can still be pending."""
        deadline = time.monotonic() + timeout_s
        while True:
            status = self._native_status(
                await self._control(device_id, state, "mcp_status")
            )
            if status == "connected":
                return
            if status != "pending" or time.monotonic() >= deadline:
                raise ScreenSetupError("Released native MCP did not connect")
            await asyncio.sleep(0.1)

    async def recover_native_tools(
        self, topic_id: uuid.UUID, agent_handle: str | None = None
    ) -> bool:
        """Put this room's platform tools back, and say whether they were gone.

        A turn that publishes nothing is the symptom: 芝士 answered where nobody
        reads and its `chat_send` call failed with `No such tool available`, so
        the room heard silence. The MCP server can be gone while the session
        reports ready (observed 2026-09-13/14), and the reconnect that fixes it
        is the same control a release sends through the runner. Asked only
        AFTER such a turn, so a healthy room pays nothing.

        True means the tools were missing and are back — the caller re-delivers
        the message. False means nothing was wrong, or the question could not be
        asked here (no screen, no runner), which is never a reason to resend.
        """
        screen = next(
            (
                screen
                for screen in self._hub.all_online_screens()
                if screen.topic_id == topic_id
                and screen.project_id is not None
                and agent_handle in (None, screen.agent_handle)
            ),
            None,
        )
        if screen is None or screen.project_id is None:
            return False
        state = machine_launcher.state_dir(
            screen.project_id,
            screen.resource_id or topic_id,
            CLAUDE_CODE,
            screen.agent_handle,
        )
        try:
            status = await self._control(screen.device_id, state, "mcp_status")
            if self._native_status(status) == "connected":
                return False
            await self._control(screen.device_id, state, "mcp_reconnect")
            await self._await_native_connected(screen.device_id, state)
        except ScreenSetupError:
            logger.exception("native tool recovery failed topic=%s", topic_id)
            return False
        logger.warning(
            "native tools were disconnected and have been reconnected topic=%s",
            topic_id,
        )
        return True

    def _link_failure(
        self, device_id: str, *, step: str, waited_s: float, offline: bool
    ) -> str:
        """One line naming the step, the machine, and what the link looked like at
        that moment — what turns a bare timeout into something a person can act
        on. Reads only what the hub already holds (no database on a failing path)."""
        name = self._hub.device_name(device_id)
        who = f"机器「{name}」" if name != device_id else f"机器 {device_id}"
        if name != device_id:
            who = f"{who}（{device_id}）"
        if offline:
            link = "连接器不在线"
        else:
            age = self._hub.last_seen_age(device_id)
            link = (
                "连接器在线，但从没收到过它的任何一帧"
                if age is None
                else f"连接器在线，最近一帧是 {age:.0f} 秒前"
            )
        return f"{step}时{who}{waited_s:.0f} 秒没有应答；{link}"

    async def _ensure_screen(
        self,
        *,
        device_id: str,
        agent_user_id: int,
        agent_handle: str,
        project_id: uuid.UUID,
        topic_id: uuid.UUID,
        token: str,
        env: dict[str, str] | None,
        launch: MachinePlan,
        environment_before: dict | None = None,
    ) -> HubScreen:
        """Reuse the topic's screen on the device, or open a fresh one running
        the harness's runner (the device-side launcher creates its home/work
        dirs). A reused screen is RE-ASSERTED, not trusted:
        the hub's registry can outrun what the connector knows (a restarted
        connector has forgotten every sid until a create makes it re-adopt the
        tmux session that outlived it; a create sent on a dying transport was
        never delivered at all), and the connector has no screen to run for a sid
        it does not know — so a turn that trusted the registry alone waited on a
        runner that was never started whenever the two had diverged. The adopt-create is
        idempotent on the device: a live session keeps running, and keeps the
        system prompt it launched with (the launcher only reads it at screen
        creation); a lost one is respawned under the same sid + screen token.

        But adopt-create only respawns a screen the CONNECTOR forgot (it restarted);
        it cannot respawn one whose runner died while the connector kept running,
        because the connector still holds the sid and merely hot-reloads into the
        dead pane. So a reused screen's runner is asked first (``_runner``); one
        that is not there is closed and reopened under a fresh sid the connector
        must Spawn, rather than reasserted into a corpse."""
        started = time.monotonic()

        def mark(phase: str) -> None:
            logger.info(
                "device_screen_timing topic=%s phase=%s elapsed_ms=%.3f",
                topic_id,
                phase,
                (time.monotonic() - started) * 1000,
            )

        resource_id = uuid.UUID((env or {}).get("CHEESE_RESOURCE_ID", str(topic_id)))
        existing = self._existing_screen(device_id, topic_id, resource_id)
        execution_target = None
        if (env or {}).get("CHEESE_EXECUTION_TARGET"):
            execution_target = json.loads((env or {})["CHEESE_EXECUTION_TARGET"])
        if (
            existing is not None
            and (env or {}).get("CHEESE_ENVIRONMENT")
            and not execution_target
        ):
            status = environment_before
            if status is None:
                status = await environment_status(
                    self._hub, device_id, project_id, resource_id
                )
            if status["state"] == "preparing":
                return existing
        agent_configuration = (env or {}).get("CHEESE_AGENT_CONFIG", "")
        if existing is not None and (
            existing.closing or self._credential_is_stale(existing)
        ):
            # A running CLI retains its birth credential. Confirm the old
            # process stopped before opening its replacement with a fresh one.
            await self._retire_screen(
                existing,
                topic_id=topic_id,
                reason="closing" if existing.closing else "credential_expiring",
            )
            existing = None
        # Device-side paths (the launcher mkdir -p's them). Kept under a stable per
        # project/topic root so the screen's git-backed work persists across turns.
        # The home is per room: the session's config, transcripts and credentials
        # live in it, and two rooms sharing one would share a conversation.
        home_dir = device_home_dir(project_id, resource_id)
        work_dir = self._work_dir(project_id, resource_id)
        api_base = await self._device_api_base(device_id)
        # 一台机器只有一种启动环境（结论 46）。Every request from every machine
        # reached this way runs through the metering proxy — an enrolled device
        # and a leased Cloud box alike, since both are launched from right here
        # (#325 G2) — and the proxy asks `/llm/admission` per request whether it
        # goes to the subscription pool or is rewritten to the gateway. There is
        # no second shape and no fallback: falling back silently is exactly the
        # model swap this kills (dev shipped device screens with
        # CLAUDE_MODEL=deepseek-chat while users thought they were talking to
        # Claude). This is what ``builds_model_env`` declares.
        #
        # The Claude login is the host's own, established by the launch script;
        # the scoped cheese token below only proves which project to bill.
        ca_pem = _read_proxy_ca()
        # Session-length TTL, not the 1h default. This token is baked into the
        # bare process's HTTPS_PROXY (CONNECT credential), read ONCE at process
        # start and never hot-refreshed; the screen is reused across turns (a
        # reassert only re-attaches, it does not relaunch claude). A 1h token
        # thus expires under a still-running process, and every turn after the
        # first hour is rejected by the metering proxy (407) — the agent looks
        # dead. Same session lifetime as the CHEESE_TOKEN minted alongside it.
        session_token = mint_scoped_token(
            project_id=str(project_id),
            topic_id=str(topic_id),
            ttl_s=SESSION_TOKEN_TTL_S,
            resource_id=str(resource_id),
            # WHO acts with it — a room cannot answer that once it may seat
            # more than one agent, so the launcher, which knows, says it.
            agent_handle=agent_handle,
        )
        tunnel_url = settings.subscription_tunnel_url.strip()
        via_tunnel = uses_tunnel(tunnel_url=tunnel_url)
        connect_proxy_url = connect_transport(
            session_token=session_token, via_tunnel=via_tunnel
        )
        sub = provider_env.subscription_provider(
            ca_path=_DEVICE_PROXY_CA_PATH,
            project_id=str(project_id),
            topic_id=str(topic_id),
            connect_proxy_url=connect_proxy_url,
            no_proxy=self._no_proxy_hosts(),
        )
        # 启动环境里没有模型这件事。Which model a request runs on is decided at
        # one control point — admission, when the request reaches the metering
        # proxy (结论 46) — and the proxy writes the answer into the REQUEST
        # BODY on its way out. So nothing here names a model: not
        # `claude --model`, not the three family aliases the CLI addresses
        # subagents by. A key here would be a second declaration of what the
        # binding on the card already says, and nobody could write down which
        # of the two wins (I4a).
        model_env = {**(env or {})}
        # Dropped, not overridden: `subscription_provider` only ADDS keys, and
        # any of these surviving from a caller's env flips the CLI out of
        # subscription mode or asks a pool for a model nobody bound.
        for key in (
            "ANTHROPIC_BASE_URL",
            "CLAUDE_MODEL",
            "ANTHROPIC_DEFAULT_HAIKU_MODEL",
            "ANTHROPIC_DEFAULT_SONNET_MODEL",
            "ANTHROPIC_DEFAULT_OPUS_MODEL",
        ):
            model_env.pop(key, None)
        model_env.update(sub.env)
        # The tunnel's CONNECT credential must carry the same place claims as
        # the direct proxy URL.
        model_env["CHEESE_CONNECT_TOKEN"] = session_token
        # Read by Claude Code only when its login comes from the environment (a
        # host with a setup-token): the scopes it then believes it holds. RC is
        # served by Cheese, not Anthropic, so the sessions scope enables it
        # whatever the token grants upstream. A stored login has its own.
        model_env["CLAUDE_CODE_OAUTH_SCOPES"] = (
            "user:inference user:profile user:sessions:claude_code"
        )
        # The meter accepts model hosts, not package registries.
        model_env["CHEESE_MODEL_PROXY"] = "1"
        if via_tunnel:
            # Read by the launch script: it writes the helper and the token
            # file, starts the helper before `claude`, and exports the port the
            # helper bound as HTTPS_PROXY. Carried on the env rather than as
            # arguments because a remote machine's launch is built entirely from
            # `extra_env` — there is no other channel into that builder.
            model_env["CHEESE_TUNNEL_URL"] = tunnel_url
        # Preserve the birth expiry across device and backend restarts.
        credential_expires = _credential_expiry(session_token)
        model_env["CHEESE_TOKEN_EXPIRES"] = str(credential_expires)
        _warn_if_model_endpoint_is_box_local(model_env, device_id)
        # 运行环境预览's dial-out address. Derived from the base this machine already
        # reaches for git and the CLI rather than configured separately:
        # the preview rides the path the connector proved, so a deployment that
        # can host a device can host a preview with nothing further to set.
        model_env["CHEESE_PREVIEW_URL"] = _preview_ws_url(api_base)
        mark("configuration_ready")
        # 跑什么，问计划要 —— 这个 channel 只说「在哪」。
        # Everything below is a fact about this room and this machine; what any
        # of it means is the harness's to decide. Until this call existed the
        # answer was assembled here, out of a script that said `claude`.
        place = MachinePlace(
            home=home_dir,
            workdir=work_dir,
            store=device_store_dir(project_id),
            # Where this harness keeps the session's state on that machine,
            # AS THE CONNECTOR RESOLVES IT: the backend records this string
            # and later derives a socket from it, so it is a fact about the
            # machine and belongs on this side of the seam.
            state=machine_launcher.state_dir(
                project_id, resource_id, launch.harness, agent_handle
            ),
            api_base=api_base,
            project_id=str(project_id),
            topic_id=str(topic_id),
            agent_handle=agent_handle,
            execution_target=execution_target,
            ca_pem=ca_pem,
        )
        holes = launch.on(place)
        command, screen_env = machine_launcher.screen_launch(place, holes, token=token)
        screen_env.update(model_env)
        if execution_target is not None:
            # The assigned executor already owns the checkout and its environment.
            for name in (
                "CHEESE_GIT_BRANCH",
                "CHEESE_BRANCH_URL",
                "CHEESE_ENVIRONMENT",
                "CHEESE_PREVIEW_URL",
                "CHEESE_PREVIEW_UP",
            ):
                screen_env.pop(name, None)
        configuration = _launch_identity(
            agent_configuration=agent_configuration,
            harness_contract=holes.contract,
            execution_target=execution_target,
        )
        # The machine reports its environment back, so this is the one value
        # both sides of the seam can be asked for. Exported for every harness,
        # not only the ones with an executor: a screen adopted after a backend
        # restart is re-read from here, and one that came back without it read
        # as a configuration change on its next turn, every turn.
        screen_env["CHEESE_AGENT_CONFIG"] = configuration
        if existing is not None and existing.agent_configuration != configuration:
            # Asked only now: what a session was started with is not fully
            # known until the harness has been asked. Closing the session ends
            # whatever it is still doing — a turn, a background command, a
            # subagent, a workflow — so while it is doing any of those, this
            # turn runs on it as it is and the relaunch waits for the first
            # turn that finds it idle. A background command may run for days,
            # so refusing turns until it ends is not waiting. A runner that
            # cannot be asked has nothing left running to lose.
            status = await self._runner(device_id, place.state, "ping")
            if status is not None and (status.get("working") or status.get("tasks")):
                logger.info(
                    "device_screen_relaunch_deferred topic=%s sid=%s working=%s "
                    "tasks=%s",
                    topic_id,
                    existing.sid,
                    bool(status.get("working")),
                    sorted((status.get("tasks") or {}).values()),
                )
                # Still what the running process was started with.
                screen_env["CHEESE_AGENT_CONFIG"] = existing.agent_configuration
            else:
                await self._retire_screen(
                    existing, topic_id=topic_id, reason="agent_configuration_changed"
                )
                existing = None
        mark("launcher_built")
        # The remote-execution helpers are the executor harness's; a session
        # whose plan has no executor has none of them to release.
        release_state = (
            {}
            if existing is not None
            and execution_target
            and isinstance(launch, ExecutorPlan)
            else None
        )
        if existing is None:
            command = await self._ship_launcher(
                device_id,
                resource_id,
                command,
                home_dir,
                execution_token=(
                    screen_env["CHEESE_TOKEN"] if execution_target is not None else None
                ),
            )
        else:
            # These device requests are independent. Finish all three before
            # adopting or replacing the screen, without adding their round trips.
            execution_token = (
                screen_env["CHEESE_TOKEN"] if execution_target is not None else None
            )
            status, tunnel_down, _ = await asyncio.gather(
                self._runner(device_id, place.state, "ping"),
                self._tunnel_helper_is_down(existing, home_dir),
                self._refresh_screen_files(
                    device_id,
                    home_dir,
                    release_state=release_state,
                    execution_token=execution_token,
                ),
            )
            retire_reason = None
            if status is None or not status.get("alive"):
                retire_reason = "session_not_alive"
            elif tunnel_down:
                retire_reason = "tunnel_helper_down"
            elif release_state is not None and self._resident_release_due(
                release_state
            ):
                # Releasing in place is an optimisation: a fresh launch starts
                # on the current release. So a process the release cannot reach
                # (its runner cannot be asked right now), or one it failed to
                # reach, is replaced instead of failing the turn — and failing
                # it again on every turn after.
                if "unknown" in status:
                    retire_reason = "resident_release_unreachable"
                else:
                    try:
                        await self._refresh_resident(
                            existing, home_dir, place.state, release_state
                        )
                    except ScreenSetupError:
                        logger.warning(
                            "resident release failed topic=%s sid=%s",
                            topic_id,
                            existing.sid,
                            exc_info=True,
                        )
                        retire_reason = "resident_release_failed"
            if retire_reason is not None:
                # Adopt-create cannot restart a dead process, its tunnel or its
                # release while the connector still knows the sid. A new sid
                # runs the launcher, which is why it is shipped only now.
                await self._retire_screen(
                    existing, topic_id=topic_id, reason=retire_reason
                )
                existing = None
                command = await self._ship_launcher(
                    device_id,
                    resource_id,
                    command,
                    home_dir,
                    execution_token=execution_token,
                )
            else:
                # The adopt-create below re-runs the launcher only for a session
                # the connector lost; the file the previous turn wrote is still
                # there for that, and the reuse gate retires a process born from
                # an expired credential on the next turn.
                command = _launcher_command(resource_id)
        mark("device_checks_complete")
        if existing is not None:
            # A reassert keeps the CURRENTLY-RUNNING session, which still holds the
            # credential it was born with — so the recorded birth expiry must NOT be
            # overwritten with this launch's freshly-minted one (the new token never
            # reaches the running process). It stays as the reuse gate's truth.
            await self._hub.reassert_screen(existing, command=command, env=screen_env)
            mark("screen_reasserted")
            existing.execution_target = execution_target
            updated = self._hub.update_screen(
                existing.sid,
                resource_id=existing.resource_id,
                execution_target=execution_target,
            )
            if inspect.isawaitable(updated):
                existing = await updated
            return existing
        screen = await self._hub.open_screen(
            device_id,
            command,
            agent_user_id=agent_user_id,
            agent_handle=agent_handle,
            project_id=project_id,
            topic_id=topic_id,
            env=screen_env,
        )
        mark("screen_opened")
        # Record what credential this freshly-Spawned session was born with, so a
        # later turn's reuse gate (and the zero-output fuse) can tell a live
        # credential from a dead one without re-deriving it.
        screen.credential_expires = credential_expires
        screen.agent_configuration = configuration
        screen.resource_id = resource_id
        screen.execution_target = execution_target
        updated = self._hub.update_screen(
            screen.sid,
            resource_id=resource_id,
            execution_target=execution_target,
            credential_expires=credential_expires,
            agent_configuration=configuration,
        )
        if inspect.isawaitable(updated):
            screen = await updated
        return screen

    # --- turn --------------------------------------------------------------

    def _sessions(self):
        """一条数据库连接。Cloud 通道从这里继承它——同一个问题，同一份答案。"""
        factory = self._session_factory
        if factory is None:
            from app.core.db import async_session_factory

            factory = async_session_factory
        return factory()

    async def _resolve_session_host(self, db, session: SessionRef) -> str:
        """读取这条会话自己的机器。在线等待由调用方在归还数据库连接后完成。

        机器从会话行上读，不是项目钉住的那台工作机。问的是这条会话而不是这个房间：
        一间房里的两个队友各有一条会话，可能坐在两台机器上。
        """
        from app.domain.agent_session.services import AgentSessionService

        await TopicService(db).get_or_404(session.topic_id)
        place = await AgentSessionService(db).place(
            session.topic_id, session.agent_handle, harness=session.harness
        )
        host = place.machine if place else settings.agent_session_device_id
        if not host:
            logger.error("session_host_unconfigured topic=%s", session.topic_id)
            raise ScreenSetupError("这条会话的机器尚未配置或未连接")
        return host

    async def _wait_for_session_host(self, host: str, session: SessionRef) -> None:
        """Give the pinned connector time to reconnect after an ingress reload.

        Call after releasing the database session: simultaneous room starts
        must leave the connection pool available while transport recovers.
        """
        if self._hub.is_online(host):
            return
        logger.info(
            "session_host_reconnecting topic=%s host=%s", session.topic_id, host
        )
        deadline = time.monotonic() + _SESSION_RECONNECT_GRACE_S
        while not self._hub.is_online(host):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                logger.warning(
                    "session_host_offline topic=%s host=%s", session.topic_id, host
                )
                raise ScreenSetupError("这条会话的机器尚未配置或未连接")
            await asyncio.sleep(min(_SESSION_RECONNECT_POLL_S, remaining))

    async def _session_agent(self, db, session: SessionRef):
        """Resolve the selected conversation's project handle to its author."""
        from app.domain.agent_instance.services import AgentInstanceService
        from app.domain.project.services import ProjectService

        if not session.agent_handle:
            return await IdentityService(db).ensure_room_agent_user(session.topic_id)
        project = await ProjectService(db).get_or_404(session.project_id)
        agents = AgentInstanceService(db)
        instance = await agents.for_handle(project, session.agent_handle)
        handle = await agents.ensure_identity(instance)
        user = await user_by_handle(db, handle)
        assert user is not None
        return user

    async def _session_host_agent(self, session: SessionRef) -> Placement:
        """不租手的一轮落在哪 (结论 19，不变量 I2)：这条会话自己的机器，加上答
        这间房的那个 agent。

        身份不是从执行机上取的，所以所有工作机离线时它照样答得出来。三条通道问的
        是同一个问题，答案就只有这一份。
        """
        async with self._sessions() as db:
            host = await self._resolve_session_host(db, session)
            agent = await self._session_agent(db, session)
            await db.commit()
            placement = Placement(host, agent.id, agent.username, rented=False)
        await self._wait_for_session_host(host, session)
        return placement

    async def precheck(self, session: SessionRef, *, needs_place: bool) -> Placement:
        """Resolve the topic's pinned/online device + its agent identity before
        anything is started on it. The resolved tuple is handed back to
        ``ensure_ready`` via ``precheck``. Raises ``ScreenSetupError`` (offline
        pinned device, or none online).

        一轮不租手时解析的是这条会话自己的机器，不是项目钉住的工作机。pi 直接用
        这条通道 (它是唯一没有包在 ``CentralChannel`` 外面的 backend)，所以「要不
        要一双手」这一问在这里也必须答得出来——答不出来，一间私聊就会因为项目没
        有在线工作机而整轮开不起来，正是 I2 要禁止的那件事。答案随 ``Placement``
        交给 ``ensure_ready``，由它决定这一轮开在草稿区还是项目工作区。"""
        if not needs_place:
            return await self._session_host_agent(session)
        resolved = await self._resolve_device_agent(
            session.project_id, session.topic_id
        )
        if resolved is None:
            raise ScreenSetupError(self.no_machine_message)
        if self._device_resolver is not None:
            return Placement(*resolved, rented=True)
        async with self._sessions() as db:
            agent = await self._session_agent(db, session)
            await db.commit()
            return Placement(resolved[0], agent.id, agent.username, rented=True)

    async def ensure_ready(
        self,
        *,
        session: SessionRef,
        token: str,
        env: dict[str, str] | None,
        memory_scope: str | None,
        owner: str | None,
        turn_id: uuid.UUID | None,
        launch: MachinePlan,
        precheck: object,
    ) -> HubScreen:
        """Reuse/open the topic's screen on the device resolved by ``precheck``;
        return the screen (ctx). Raises ScreenSetupError when the screen fails.

        What runs in that screen is the plan's answer, not this file's: a device
        launch is a shell script, and the platform half of it is the same for
        every harness (``machine_launcher``) while the harness fills the rest.
        This channel says where — the home, the workdir, the state directory the
        connector will resolve — and merges the two environments."""
        assert isinstance(precheck, Placement)  # from our precheck
        project_id, topic_id = session.project_id, session.topic_id
        device_id = precheck.machine
        agent_user_id, agent_handle = precheck.agent_user_id, precheck.agent_handle
        rented = precheck.rented
        # 记忆算谁的，只决定记忆算谁的。这一轮开在哪个工作区是 ``rented`` 的事，
        # 下面那一句说；两个事实各说各的，其中一个换了另一个不跟着动。
        if memory_scope == "personal":
            env = dict(env or {}, CHEESE_MEMORY_SCOPE="personal")
            if owner:
                env["CHEESE_OWNER"] = owner
        prepares_environment = (
            bool((env or {}).get("CHEESE_ENVIRONMENT"))
            and rented
            and not (env or {}).get("CHEESE_EXECUTION_TARGET")
        )
        try:
            from app.core.db import async_session_factory

            factory = self._session_factory or async_session_factory
            # Read which generation of the room this is, then let the connection
            # go: nothing below writes, and every path into `ensure_ready` runs
            # under ChatService's per-topic lock (`_prompt_lock`), so the row
            # lock serialized nothing this process was not serializing already.
            # Held across the device work it cost a pool connection, and the
            # room's own row, for as long as starting an agent on a remote
            # machine takes — which queued every writer of that row (a title, an
            # archive, a read mark) behind it, each holding a connection of its
            # own until the start finished.
            async with factory() as room_session:
                room = await TopicService(room_session).lock_for_execution(topic_id)
                resource_id = room.resource_id or room.id
                # Use the same actor for the repository and for tools, including
                # a non-default teammate whose identity differs from the machine
                # precheck. A token that names nobody keeps the precheck identity.
                actor = token_agent_handle(token)
                if actor and actor != agent_handle:
                    user = await user_by_handle(room_session, actor)
                    if user is None:
                        raise ScreenSetupError("本轮 agent 身份不存在，无法启动执行机")
                    agent_user_id, agent_handle = user.id, user.username
            env = {**(env or {}), "CHEESE_RESOURCE_ID": str(resource_id)}
            # 这一轮没租手，所以它跑在这条会话自己的草稿区里：一个有界的一次
            # 性容器，开在会话自己的机器上，不是一个地点 (结论 19)。做这个选
            # 择的是「租到手没有」，不是「这间房是不是私聊」。
            if not rented:
                from app.domain.agent.private_chat import scratch_target

                env["CHEESE_EXECUTION_TARGET"] = json.dumps(
                    scratch_target(project_id, resource_id, device_id=device_id)
                )
            before = (
                await environment_status(self._hub, device_id, project_id, resource_id)
                if prepares_environment
                else {}
            )
            prior_screen = self._existing_screen(device_id, topic_id, resource_id)
            screen = await self._ensure_screen(
                device_id=device_id,
                agent_user_id=agent_user_id,
                agent_handle=agent_handle,
                project_id=project_id,
                topic_id=topic_id,
                token=token,
                env=env,
                launch=launch,
                environment_before=before,
            )
            if prepares_environment:
                # A process started before this feature keeps its environment
                # until its next restart; it has no preparation receipt yet.
                # Reasserting a live screen does not rerun its environment. A new
                # screen must still wait for its own preparation attempt below.
                if (
                    before.get("state") in {"pending", "ready"}
                    and screen is prior_screen
                ):
                    return screen
                try:
                    polling_started = time.monotonic()
                    start_deadline = polling_started + 60
                    async with asyncio.timeout(3660):
                        while True:
                            status = await environment_status(
                                self._hub,
                                device_id,
                                project_id,
                                resource_id,
                                wait_ready=time.monotonic() - polling_started < 10,
                            )
                            if status["state"] == "ready":
                                break
                            if status["state"] == "stopped" and status.get(
                                "attempt"
                            ) != before.get("attempt"):
                                raise ScreenSetupError(
                                    "环境已准备完成，但会话启动后退出，可以在房间终端查看原因"
                                )
                            if (
                                status["state"] == "pending"
                                or status.get("attempt") == before.get("attempt")
                                and before.get("state") != "preparing"
                            ) and time.monotonic() >= start_deadline:
                                raise ScreenSetupError(
                                    "环境执行器未启动，请查看房间终端"
                                )
                            if status["state"] == "failed" and (
                                before.get("state") == "preparing"
                                or status.get("attempt") != before.get("attempt")
                            ):
                                raise EnvironmentPreparationError(status)
                            # Fast launches should not sit behind a two-second
                            # poll; long installers keep the low-frequency checks.
                            await asyncio.sleep(
                                0.2 if time.monotonic() - polling_started < 10 else 2
                            )
                except asyncio.CancelledError:
                    await asyncio.shield(
                        environment_status(
                            self._hub, device_id, project_id, topic_id, action="cancel"
                        )
                    )
                    raise
            return screen
        except EnvironmentPreparationError:
            raise
        except Exception as exc:  # noqa: BLE001 — any setup failure ends the turn
            # str(exc) is EMPTY for a bare TimeoutError — the failure that used to
            # reach the room as 「device 后端启动失败：」 with nothing after the
            # colon. Fall back to the exception type so the message always says
            # *something* about what went wrong.
            raise ScreenSetupError(
                f"device 后端启动失败：{str(exc) or exc.__class__.__name__}"
            ) from exc

    def _credential_is_stale(self, screen: HubScreen) -> bool:
        """Whether the credential this screen's `claude` was LAUNCHED with has
        expired, or is within the retire margin of it (#388 缺陷二).

        The freshness of that credential is part of whether a screen may be REUSED,
        not just whether its process is alive: `claude` reads its model credential
        (HTTPS_PROXY CONNECT password / CLAUDE_CODE_OAUTH_TOKEN) exactly once at
        startup, and a reused screen is only reasserted (an adopt-create),
        never relaunched — so a still-running process on a dead credential is
        rejected on every request while its runner keeps reporting it alive. This
        is the local, in-memory half of the gate; it never touches the device.

        Conservative in the same direction as the runner check: a screen with no
        recorded expiry (`None` — adopted after a server restart, or a dev token
        with no decodable claim) is treated as FRESH and never retired on missing
        information, so we only ever retire a credential we can prove is dying."""
        exp = screen.credential_expires
        if exp is None:
            return False
        return exp <= int(time.time()) + _CREDENTIAL_EXPIRY_MARGIN_S

    async def _tunnel_helper_is_down(self, screen: HubScreen, home_dir: str) -> bool:
        """Whether the machine-local tunnel helper this screen's `claude` dials has
        stopped listening — the second half of the reuse gate, alongside
        `_credential_is_stale`.

        Both answer the same question about different dependencies: `claude` reads
        its HTTPS_PROXY exactly once at startup, and a reused screen is reasserted
        rather than relaunched, so a dependency that dies under the running process
        can never be repaired in place. For the credential that meant a permanent
        407; for the tunnel helper it means a permanent ConnectionRefused, with the
        runner reporting the process alive throughout.

        Skipped entirely on a deployment with no tunnel (the device dials the meter
        directly, so there is no helper to lose) — that keeps the per-turn cost at
        zero everywhere the failure cannot happen.

        Which port to ask about is the machine's answer, not ours: the helper
        bound whatever the kernel gave it and recorded it in the room's home, so
        the probe reads it from there.

        Conservative in the same direction as the runner check: only an explicit
        `down` retires a screen. An exec failure, a non-zero exit, or an `unknown`
        (no /proc, no awk, no readable port file) is read as "still up", so a
        probe hiccup never throws away a healthy screen and its in-progress
        work."""
        topic_id = screen.topic_id
        if topic_id is None:
            return False
        if not uses_tunnel(tunnel_url=settings.subscription_tunnel_url.strip()):
            return False
        started = time.monotonic()
        try:
            result = await self._hub.exec(
                screen.device_id,
                ["sh", "-c", DEVICE_TUNNEL_PROBE],
                env={"CHEESE_TUNNEL_PROBE_HOME": home_dir},
                timeout=_ALIVE_PROBE_TIMEOUT_S,
            )
        except Exception:  # noqa: BLE001 — a probe failure is not proof of death
            return False
        finally:
            logger.info(
                "device_probe_timing topic=%s probe=tunnel elapsed_ms=%.3f",
                topic_id,
                (time.monotonic() - started) * 1000,
            )
        if result.get("exit") != 0:
            return False
        return (result.get("stdout") or "").strip() == "down"


async def list_device_storage(
    device_id: str, *, hub: DeviceHub | None = None
) -> list[tuple[str, str, str]]:
    """Every ``(kind, project, place)`` under both device storage roots,
    as the device's shell sees them — names only, nothing resolved.

    Raises ``DeviceOffline`` like ``exec`` does; the caller decides what an
    unreachable device means for its sweep. Lists with a shell loop rather than
    `find -printf`, which is GNU-only and a device may be a Mac."""
    hub = hub or device_hub
    script = (
        f'for root in "{DEVICE_HOME_ROOT}" "{DEVICE_WORK_ROOT}"; do '
        '(cd "$root" 2>/dev/null || exit 0; '
        # A project/place symlink may point into the device owner's other data.
        'for p in */*; do if [ -d "$p" ] && '
        '[ ! -L "${p%%/*}" ] && [ ! -L "$p" ]; then '
        'printf "%s\\t%s\\n" "${root##*/}" "$p"; fi; done); done'
    )
    result = await hub.exec(device_id, ["sh", "-lc", script], timeout=30)
    if result.get("exit") != 0 or result.get("truncated"):
        raise RuntimeError("device storage listing failed or was truncated")
    pairs: list[tuple[str, str, str]] = []
    for line in str(result.get("stdout") or "").splitlines():
        kind, tab, path = line.partition("\t")
        project, sep, place = path.partition("/")
        if kind in {"home", "work"} and tab and sep and project and place:
            pairs.append((kind, project, place))
    return pairs


def topic_credential_expiry(
    topic_id: uuid.UUID, *, hub: DeviceHub | None = None
) -> int | None:
    """The UNIX expiry of the model credential the topic's LIVE device screen was
    launched with, or ``None`` when the topic has no online device screen (or its
    expiry was never recorded).

    The zero-output fuse (``runtime``) reads this to tell a turn that is doomed
    BECAUSE its baked credential is already dead — a live `claude` being fed
    messages and rejected on every one (#388 缺陷一) — from a generic cold start,
    so it can fast-fail with the true reason instead of burning the full fuse on a
    guess. Backend-agnostic by construction: a topic running on the local tmux/SDK
    path has no device screen here, so this returns ``None`` and the fuse is
    unchanged for it. Returns the SOONEST expiry across the topic's online screens
    (there is normally one — a topic pins to a single device)."""
    hub = hub or device_hub
    exps = [
        screen.credential_expires
        for screen in hub.screens_for_topic(topic_id)
        if hub.is_online(screen.device_id) and screen.credential_expires is not None
    ]
    return min(exps) if exps else None
