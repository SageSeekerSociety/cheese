"""DeviceChannel — the self-hosted / BYO-compute channel (P3).

The screen lives on a *user's own enrolled
machine* instead of a platform container. The platform opens it over the frozen
``link.Msg`` channel (``DeviceHub``) and the device runs ``claude`` with our
hooks (``device_launch``), so events come back through the SAME hook path
(``/sandbox/hooks/{topic}`` → ``hook_router`` → ``translate_hook``) the tmux
channel uses. The prompt is delivered over the screen's rendezvous socket
function — not by reading/writing the screen from the backend.

Per request:
  1. resolve an online device bound to the project + its agent identity (DB),
  2. ensure a screen for the topic on that device (open via ``DeviceHub`` if absent),
  3. register the topic's hook queue, then deliver the prompt over rendezvous,
  4. drain the hook queue, translating each hook to an ``AgentEvent`` (reused verbatim),
  5. let the device commit and push its own worktree back over git smart-HTTP.
"""

import asyncio
import base64
import hashlib
import inspect
import json
import logging
import shlex
import time
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import settings
from app.core.sandbox_auth import mint_scoped_token, scoped_token_claims
from app.domain.agent import machine_launcher, provider_env
from app.domain.agent.device_hub import (
    DeviceHub,
    DeviceOffline,
    HubScreen,
    device_hub,
)
from app.domain.agent.harness.channel import Channel, ScreenSetupError
from app.domain.agent.harness.claude_code import (
    DEVICE_ALIVE_PROBE,
    DEVICE_TUNNEL_PROBE,
    SESSION_TOKEN_TTL_S,
    resident_release,
)
from app.domain.agent.harness.launch import MachinePlace, MachinePlan
from app.domain.agent.hook_forwarder import CHEESE_HOOK_SCRIPT
from app.domain.agent.platform_failures import (
    DEVICE_OFFLINE_MESSAGE,
    HOST_UNREACHABLE_CODE,
)
from app.domain.device.models import DeviceRow
from app.domain.device.service import DeviceService
from app.domain.device.supply import (
    Supply,
    default_visibility,
    has_runnable_transport,
)
from app.domain.device.wiring import sql_device_service
from app.domain.identity.services import IdentityService
from app.domain.topic.services import TopicService
from app.domain.workspace import service as ws

# Resolve the device a turn runs on for (project, topic) → (device_id, agent_user_id,
# agent_handle). Takes both ids because the device is chosen with topic affinity, not
# just per project (execution-architecture v4 §affinity).
logger = logging.getLogger(__name__)

# How long the launcher file write may go unanswered. The hub adds 5s of grace
# on top for the exec.result frame itself.
_LAUNCHER_SHIP_TIMEOUT_S = 30

DeviceResolver = Callable[
    [uuid.UUID, uuid.UUID], Awaitable["tuple[str, int, str] | None"]
]


class EnvironmentPreparationError(ScreenSetupError):
    def __init__(self, status: dict):
        self.environment_status = status
        super().__init__(
            "环境准备失败，芝士还没有开始处理这条消息。",
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
    healthy = await service.healthy_devices_for_project(project_id, is_online)
    for device in healthy:
        # The same fact the market catalogue publishes as `default=True`, read from
        # one place so the picker can never advertise a 档 the resolver does not
        # bind. Today that resolves to `host`, because `isolated` has no transport;
        # when #358 step 2 supplies one, this and the catalogue move together.
        await service.bind_topic_device(
            topic_id, device.device_id, visibility=default_visibility()
        )
        return device.device_id
    return None


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


def tunnel_port_for_topic(topic_id: uuid.UUID) -> int:
    """The tunnel helper's loopback port for THIS topic — derived, not fixed.

    One fixed port (#425) meant two concurrent topics on one remote machine
    raced for the same bind: the second helper failed and its turns died
    looking like a dead model. Deriving from the topic id keeps the port
    stable across screen reuse/reassert (claude bakes its HTTPS_PROXY at
    launch and never re-reads it, #385) while giving concurrent topics
    distinct listeners. Collisions inside the 2000-port window are possible
    but loud: the second helper's bind fails and the launch surfaces a
    visible setup error instead of a silent share.
    """
    base = settings.subscription_tunnel_local_port
    return base + (int(hashlib.sha1(str(topic_id).encode()).hexdigest(), 16) % 2000)


def _preview_ws_url(public_base: str) -> str:
    """``wss://…/preview/tunnel`` for a machine, from the base it already dials.

    Scheme-swapped rather than configured: the connector, the hooks and the CLI
    all reach this origin already, so a preview that rides the same one needs no
    second address to keep true — and a deployment cannot end up with a preview
    pointed somewhere the machine was never able to reach.
    """
    base = public_base.rstrip("/")
    for http_scheme, ws_scheme in (("https://", "wss://"), ("http://", "ws://")):
        if base.startswith(http_scheme):
            base = ws_scheme + base[len(http_scheme) :]
            break
    return f"{base}/preview/tunnel"


def connect_transport(
    *, session_token: str, via_tunnel: bool, tunnel_port: int | None = None
) -> str:
    """The ``HTTPS_PROXY`` value that steers this screen to the meter.

    Through the tunnel the address is loopback and carries NO credential: the
    helper is the only thing listening there, and it reads the scoped token from
    a file the launcher writes — kept out of the URL so a refreshed token takes
    effect without relaunching `claude`, which reads this value exactly once at
    startup (#385).

    Direct, the scoped token rides as the proxy password, which is what stops an
    exposed listener relaying for anyone who cannot prove which project to bill.
    """
    if via_tunnel:
        port = tunnel_port or settings.subscription_tunnel_local_port
        return f"http://127.0.0.1:{port}"
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
    value: the model and role it was born with, the harness build and the argv
    it was started with, the executor it was handed, and the directory the
    platform installed itself into. Anything left out is a change that lands in
    the code and never reaches the rooms already running — the shape this
    replaced compared only the first of the four, so a pinned harness version
    could move while every reused screen kept the one it started with, and
    nothing said so.
    """
    return hashlib.sha256(
        json.dumps(
            {
                "agent": agent_configuration,
                "harness": harness_contract,
                "target": execution_target,
                "root": machine_launcher.PLATFORM_DIR,
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()


def _warn_if_model_endpoint_is_box_local(env: dict[str, str], device_id: str) -> None:
    # ANTHROPIC_BASE_URL is the gateway route; HTTPS_PROXY is the subscription's
    # CONNECT route to the metering proxy. Either one pointing at a box-local
    # address fails identically off-box.
    for key in ("ANTHROPIC_BASE_URL", "HTTPS_PROXY"):
        value = env.get(key, "")
        # A configured tunnel intentionally points HTTPS_PROXY at the helper on
        # the device's own loopback; that address is not a backend-host leak.
        if key == "HTTPS_PROXY" and env.get("CHEESE_TUNNEL_URL"):
            continue
        if any(h in value for h in _BOX_LOCAL_HOSTS):
            logger.error(
                "device %s received %s=%s, which only "
                "resolves on the backend's own host — its turns will fail to "
                "reach a model. Give devices a reachable address "
                "(subscription_device_proxy_host for the metering proxy) or "
                "configure subscription_tunnel_url.",
                device_id,
                key,
                value,
            )


def _read_proxy_ca() -> str:
    """The metering proxy's CA, read where THIS backend can see it — required for
    a subscription device turn (the launcher embeds it; without it the screen's
    `claude` cannot trust the proxy and fails as an opaque TLS error). Raising
    here, with the setting named, beats the silent alternative: falling back to
    the gateway would swap the model out from under the user — the exact failure
    #325 G2 removes."""
    path = settings.subscription_ca_backend_path.strip()
    if not path:
        raise ScreenSetupError(
            "subscription_enabled 但未设置 SUBSCRIPTION_CA_BACKEND_PATH——"
            "device 屏幕需要后端能读到计费代理的 CA（部署侧把代理的 "
            "mitmproxy-ca-cert.pem 只读挂载进后端并指向它）"
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


# How long the idle-suspect liveness probe (DEVICE_ALIVE_PROBE over the link
# `exec`) may take. Short by design — well under hooks_substrate's CONFIRM_POLL_S
# (15s) so a suspected-wedged turn re-probes on cadence — and a timeout/hiccup is
# read as alive, never as death (see `confirm_alive`).
_ALIVE_PROBE_TIMEOUT_S = 8.0

# Retire-and-reopen a reused screen whose baked credential is within this many
# seconds of expiry, before reusing a running device session
# so the backend's reuse decision and the on-device create gate agree on ONE
# margin. Small on purpose: it only rejects an already-dead-or-dying credential,
# never a healthy one, so a short-lived token (the gateway path's hour) is
# re-minted at most once per margin rather than on every turn.
_CREDENTIAL_EXPIRY_MARGIN_S = 300

# How long to wait for the connector's delivery verdict. It must exceed the
# connector's own budget for a COLD screen — dial the socket a booting claude has
# not bound yet (120s) plus the launcher's token file (20s) — or the backend
# gives up first and reports a failure while delivery is still in flight, which
# is a false alarm indistinguishable from a real one. Warm screens answer in
# milliseconds; this ceiling only ever costs anything on the first turn.
_PROMPT_DELIVERY_TIMEOUT_S = 180

# How long to wait for a staged file's ack. Much shorter than the prompt's
# budget, deliberately: staging is a write to an already-connected machine, so a
# healthy one answers in milliseconds, and the case actually worth optimising
# for is a connector that will NEVER answer because it does not know this frame.
# The message rides on without the image once this expires, so the cost of the
# wait is paid by the reader — keep it short enough that they do not feel it.
_FILE_STAGE_TIMEOUT_S = 20


def _credential_expiry(token: str) -> int:
    """Read the credential's birth expiry, retained with its device session.

    A development token without an expiry gets the normal session lifetime.
    """
    claims = scoped_token_claims(token)
    exp = claims.get("exp") if claims else None
    if isinstance(exp, int):
        return exp
    return int(time.time()) + SESSION_TOKEN_TTL_S


# Where a place's isolated claude home lives on the device, relative to the
# device's own `$HOME` (expanded by its shell, never by us). The path is spelled
# in one place because two sides depend on it agreeing: the launcher that
# creates it and the retirement that removes it (topic/retire.py).
DEVICE_HOME_ROOT = "$HOME/.cheese/home"
DEVICE_WORK_ROOT = "$HOME/.cheese/work"
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
DEVICE_STORE_ROOT = "$HOME/.cheese/store"


def device_home_dir(project_id: uuid.UUID, place_id: uuid.UUID) -> str:
    return f"{DEVICE_HOME_ROOT}/{project_id}/{place_id}"


def device_work_dir(project_id: uuid.UUID, place_id: uuid.UUID) -> str:
    return f"{DEVICE_WORK_ROOT}/{project_id}/{place_id}"


def device_store_dir(project_id: uuid.UUID) -> str:
    return f"{DEVICE_STORE_ROOT}/{project_id}"


# Where a place's environment runner may have been left, relative to that
# place's home, in precedence order. Every root the platform has ever installed
# into belongs here, because a place prepared under an earlier one keeps the
# runner where that launcher put it until something relaunches it — and the
# status it prepared is real the whole time. Each copy is byte-identical and all
# of them keep their state in the same `$HOME/.cheese-environment/status.json`,
# so whichever one we find answers for the place. Probing only the current root
# is what made a place prepared by another launcher read as `pending` forever:
# the ready status was on disk, one directory over. Every place that WRITES the
# runner has to appear in this list — see test_environment_status_probe.py.
ENVIRONMENT_RUNNER_PATHS = (
    "$HOME/.cheese/cheese-environment.py",
    "$HOME/.claude/cheese-environment.py",
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
    reset_marker = (
        'mkdir -p "$HOME/.cheese"; touch "$HOME/.cheese/environment-restart"; '
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
    return ["bash", "-lc", f'exec bash "$HOME/.cheese/launch/{topic_id}.sh"']


class DeviceChannel(Channel):
    """The REMOTE channel: a screen on a user's enrolled machine, opened over
    the frozen link.Msg link (DeviceHub). The screen is a ``HubScreen``.

    Enrollment, device resolution, rendezvous and the launcher shipped to the
    machine are what this file is about; what runs on the screen is
    ``ClaudeCodeRuntime``."""

    name = "device"
    # ``_ensure_screen`` below builds the whole model environment on the machine
    # — the metering-proxy env under a subscription, the backend's /llm route and
    # a scoped token without one. Either way the machine holds no provider
    # credential, so the platform must hand this transport the model choice and
    # nothing else. Every transport that reaches a machine over a link inherits
    # this build, and inherits the declaration with it.
    builds_model_env = True
    needs_topic_message = "device 后端需要话题上下文（每个屏幕绑定一个话题）"
    timeout_message = "device 轮次超时"

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
        # Recovered subscriptions have no surviving HubScreen object in this
        # process. Keep their device relation so disconnect still tears them down.
        self._subscription_devices: dict[uuid.UUID, str] = {}

    def available(self) -> bool:
        """Whether any device is currently connected (online). Project-level checks
        happen per turn, in ``precheck``."""
        return bool(self._hub.online_device_ids())

    def owns(self, supply: Supply) -> bool:
        """Is a machine that entered this way THIS channel's to listen to?

        Both channels bind their topics into the same table, so a pin does not
        say which of them made it — the machine does, and ``Supply`` is the axis
        that separates them (the platform opened it → Cloud's; a human enrolled
        it → this one's). ``_resolve_device_agent`` on the Cloud side already
        refuses a machine of the wrong supply for the same reason.
        """
        return supply is not Supply.cloud

    async def discover(
        self, device_id: str | None = None
    ) -> list[tuple[uuid.UUID, uuid.UUID, object | None, str | None]]:
        """Recover this channel's room subscriptions and their surviving screens.

        DB bindings select the channel; the device supplies the running screen.
        Recovering a room through both device and cloud channels would split its
        event queue between two consumers.
        """
        online = set(self._hub.online_device_ids())
        device_ids = [device_id] if device_id in online else []
        if device_id is None:
            device_ids = sorted(online)
        if not device_ids:
            return []

        factory = self._session_factory
        if factory is None:
            from app.core.db import async_session_factory

            factory = async_session_factory
        scopes: list[tuple[uuid.UUID, uuid.UUID, str]] = []
        async with factory() as session:
            devices = sql_device_service(session)
            topics = TopicService(session)
            for connected_device_id in device_ids:
                endpoint = await devices.get_device(connected_device_id)
                if endpoint is None or not self.owns(endpoint.supply):
                    continue
                bindings = await devices.list_topic_bindings(connected_device_id)
                for binding in bindings:
                    topic = await topics.get(binding.topic_id)
                    if topic is not None:
                        scopes.append((topic.project_id, topic.id, connected_device_id))

        for _, topic_id, connected_device_id in scopes:
            self._subscription_devices[topic_id] = connected_device_id
        # No harness tag: the pin records WHICH TOPIC is bound to which device,
        # not what we started on it, and the screen itself is gone from this
        # process. Running two harnesses on one enrolled machine needs the
        # binding to record which — until then this transport hosts one.
        return await self.restore_screens(scopes)

    async def restore_screens(
        self, scopes: list[tuple[uuid.UUID, uuid.UUID, str]]
    ) -> list[tuple[uuid.UUID, uuid.UUID, object | None, str | None]]:
        """Rebuild screen identities for the rooms this channel owns in the DB."""
        inventories = {
            device_id: await self._hub.list_screens(device_id)
            for device_id in {scope[2] for scope in scopes}
        }
        if not any(inventories.values()):
            return [(project, topic, None, None) for project, topic, _ in scopes]
        factory = self._session_factory
        if factory is None:
            from app.core.db import async_session_factory

            factory = async_session_factory
        restored = []
        async with factory() as session:
            for project_id, topic_id, device_id in scopes:
                screen = None
                room = await TopicService(session).get(topic_id)
                current_resource = (room.resource_id or topic_id) if room else None
                for entry in inventories[device_id]:
                    env = entry.get("env", {})
                    if (env.get("CHEESE_PROJECT"), env.get("CHEESE_TOPIC")) != (
                        str(project_id),
                        str(topic_id),
                    ):
                        continue
                    agent = await IdentityService(session).ensure_topic_agent_user(
                        topic_id
                    )
                    expiry = env.get("CHEESE_TOKEN_EXPIRES")
                    target = env.get("CHEESE_EXECUTION_TARGET")
                    recovered = self._hub.adopt_screen(
                        device_id,
                        entry["sid"],
                        token=entry["screen"],
                        agent_user_id=agent.id,
                        agent_handle=agent.username,
                        project_id=project_id,
                        topic_id=topic_id,
                        resource_id=uuid.UUID(
                            env.get("CHEESE_RESOURCE_ID") or str(topic_id)
                        ),
                        command=entry["command"],
                        hook_key=str(topic_id),
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
            await session.commit()
        return restored

    def topics_on_device(self, device_id: str) -> list[uuid.UUID]:
        return [
            topic_id
            for topic_id, subscribed_device_id in self._subscription_devices.items()
            if subscribed_device_id == device_id
        ]

    def forget_topic(self, topic_id: uuid.UUID) -> None:
        self._subscription_devices.pop(topic_id, None)

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
            from app.domain.topic.services import TopicService

            place = await TopicService(session).place_or_404(topic_id)
            if place.room.is_private:
                from app.domain.agent.private_chat import execution_target
                from app.domain.device.supply import Visibility

                placement = place.room.session_placement
                device_id = execution_target(
                    project_id,
                    topic_id,
                    device_id=placement["device_id"] if placement else None,
                )["device_id"]
                if not self._hub.is_online(device_id):
                    raise ScreenSetupError("私聊中心执行机未连接，本轮没有启动")
                binding = await service.topic_binding(topic_id)
                if binding is None or binding.device_id != device_id:
                    await service.bind_topic_device(
                        topic_id, device_id, visibility=Visibility.host
                    )
            else:
                device_id = await resolve_pinned_device(
                    service, self._hub.is_online, project_id, topic_id
                )
            if device_id is None:
                return None
            # The screen acts as THIS topic's 分身 (its own agent-user), so a turn
            # run on a self-hosted box is attributable to the same identity as one
            # run locally — the device stays pure compute either way.
            agent = await IdentityService(session).ensure_topic_agent_user(topic_id)
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
        if (
            device_id == settings.agent_session_device_id
            and settings.agent_session_api_base
        ):
            return settings.agent_session_api_base.rstrip("/")
        factory = self._session_factory
        if factory is None:
            from app.core.db import async_session_factory

            factory = async_session_factory
        async with factory() as session:
            device = await session.get(DeviceRow, device_id)
            if (
                device
                and device.supply == Supply.cloud
                and device.cloud_control_private
            ):
                return "http://127.0.0.1:18080"
        return self._public_base

    async def _device_ccproxy_upstream(self, device_id: str) -> str:
        """The ccproxy identity this DEVICE brings, '' when it brings none.

        Non-empty means the device runs on the machine-ticket model — claude
        carries the device's own ccproxy ticket and the meter relays it over
        this identity — the same credential shape as an enrolled MicroCloud
        machine (whose identity lives on `ProjectMachine` and whose launches are
        already steered by CHEESE_TUNNEL_URL)."""
        factory = self._session_factory
        if factory is None:
            from app.core.db import async_session_factory

            factory = async_session_factory
        async with factory() as session:
            device = await sql_device_service(session).get_device(device_id)
        if device is None:
            return ""
        # Direct attribute access, not getattr-with-default: this field was added
        # to the model but not the domain dataclass at first, and a
        # getattr(..., None) fallback turned that gap into a silent "" — the
        # signal was never sent and the box looped on the swap path. A missing
        # field must now be an AttributeError at the first turn, not a quiet
        # miss (2026-08-15).
        return (device.ccproxy_upstream or "").strip()

    def _work_dir(self, project_id: uuid.UUID, topic_id: uuid.UUID) -> str:
        """The screen's cwd on the device.

        Every device owns an independent checkout. Physical host placement never
        changes this boundary: files cross it through git or `file.put`, never by
        translating a backend path into the device's namespace.
        """
        return f"{device_home_dir(project_id, topic_id)}/room"

    def _no_proxy_hosts(self) -> str:
        """What the screen's HTTPS_PROXY must NOT capture: the backend itself
        (hooks, git smart-HTTP, the `cheese` CLI) and loopback (local MCP). The
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
        path = f"$HOME/.cheese/launch/{topic_id}.sh"
        transfer, exec_env = self._screen_file_refresh(
            home_dir, release_state=release_state, execution_token=execution_token
        )
        transfer = f'mkdir -p "$HOME/.cheese/launch" && cat > "{path}" && ' + transfer
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
        """The shell that brings a screen's per-turn files up to date: the hook
        forwarder (so a reused process picks up hook fixes), the forwarded-fs
        token (rotated every turn), and a read of the release marker when the
        caller tracks one. Shared by the launcher ship and the live-screen
        refresh, so both paths write the same files the same way."""
        hook_dir = f"{home_dir}/.cheese"
        transfer = (
            f'mkdir -p "{hook_dir}"'
            f" && printf %s {shlex.quote(CHEESE_HOOK_SCRIPT)}"
            f' > "{hook_dir}/cheese-hook.next.$$"'
            f' && chmod 755 "{hook_dir}/cheese-hook.next.$$"'
            f' && mv "{hook_dir}/cheese-hook.next.$$" "{hook_dir}/cheese-hook"'
        )
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
        """A live screen keeps the `claude` it was born with and never runs its
        launcher again, so a reused turn ships only what that process will
        actually read next: the hook, the token, the release marker. The
        launcher itself is 450 KB of embedded helper sources plus the prompt,
        and sending it here bought nothing but 46 ms on every turn."""
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

    async def _refresh_resident(
        self, screen: HubScreen, home_dir: str, state: dict
    ) -> bool:
        sources = resident_release.sources()
        version = resident_release.digest(sources)
        if state.get("version") == version:
            return False
        from app.domain.agent.remote_control import store

        control = store()
        session = await control.current(str(screen.topic_id))
        if not session or session["status"] != "active":
            raise ScreenSetupError(
                "Resident release requires the active native control session"
            )
        if screen.resource_id is not None and (
            (session.get("execution") or {}).get("resource_id")
            != str(screen.resource_id)
        ):
            raise ScreenSetupError(
                "Native control belongs to another execution generation"
            )

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
        if not staged["changed"]:
            return False
        # The rendezvous path runs terminal-only slash commands without typing
        # over a person's draft. The transcript confirms plugin loading finished.
        await self.send_prompt(screen, "/reload-plugins")
        await execute("wait_reloaded", staged["offsets"])

        request = self._native_control(session)
        await request("mcp_reconnect")
        await self._await_native_connected(request)
        await execute("acknowledge", home_dir, version)
        logger.info(
            "resident release applied topic=%s version=%s", screen.topic_id, version
        )
        return True

    @staticmethod
    def _native_control(session: dict):
        """A caller for this room's live native control session: one control
        request in, its response out. The session is the terminal 芝士 is
        actually running in, which is why `mcp_reconnect` through it reaches the
        running process rather than a new one."""
        from app.domain.agent.remote_control import store

        control = store()

        async def request(subtype: str) -> dict:
            request_id = str(uuid.uuid4())
            await control.enqueue(
                session["id"],
                {
                    "type": "control_request",
                    "request_id": request_id,
                    "request": {"subtype": subtype, "serverName": "native"},
                },
                "cheese-release",
            )
            result = await control.result(session["id"], request_id, 30)
            if not result or result.get("response", {}).get("subtype") != "success":
                raise ScreenSetupError(f"Resident MCP {subtype} did not complete")
            return result["response"].get("response", {})

        return request

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

    async def _await_native_connected(self, request, timeout_s: float = 30) -> None:
        """A reconnect is acknowledged while the server can still be pending."""
        deadline = time.monotonic() + timeout_s
        while True:
            state = self._native_status(await request("mcp_status"))
            if state == "connected":
                return
            if state != "pending" or time.monotonic() >= deadline:
                raise ScreenSetupError("Released native MCP did not connect")
            await asyncio.sleep(0.1)

    async def recover_native_tools(self, topic_id: uuid.UUID) -> bool:
        """Put this room's platform tools back, and say whether they were gone.

        A turn that publishes nothing is the symptom: 芝士 answered in its
        terminal and its `chat_send` call failed with `No such tool available`,
        so the room heard silence. The MCP server can be gone while the session
        reports ready (observed 2026-09-13/14), and the reconnect that fixes it
        is the same control request a release uses. Asked only AFTER such a
        turn, so a healthy room pays nothing.

        True means the tools were missing and are back — the caller re-delivers
        the message. False means nothing was wrong, or the question could not be
        asked here (no live control session), which is never a reason to resend.
        """
        from app.domain.agent.remote_control import store

        session = await store().current(str(topic_id))
        if not session or session["status"] != "active":
            return False
        request = self._native_control(session)
        try:
            if self._native_status(await request("mcp_status")) == "connected":
                return False
            await request("mcp_reconnect")
            await self._await_native_connected(request)
        except (ScreenSetupError, TimeoutError):
            logger.exception("native tool recovery failed topic=%s", topic_id)
            return False
        logger.warning(
            "native tools were disconnected and have been reconnected topic=%s",
            topic_id,
        )
        return True

    async def _refresh_forwarded_context(
        self, screen: HubScreen, home_dir: str, target: dict
    ) -> None:
        result = await self._hub.exec(
            screen.device_id,
            ["python3", "-"],
            stdin=resident_release.script("apply_forwarded_context", home_dir, target),
            timeout=40,
        )
        if result.get("exit") != 0:
            raise ScreenSetupError(
                "Forwarded context refresh failed: " + str(result.get("stderr") or "")
            )
        status = json.loads(result["stdout"])
        if not status.get("changed"):
            return
        await self.send_prompt(screen, "/reload-skills")
        result = await self._hub.exec(
            screen.device_id,
            ["python3", "-"],
            stdin=resident_release.script(
                "wait_skills_reloaded",
                status["offsets"],
                home_dir,
                target["context_tree"]["generation"],
            ),
            timeout=40,
        )
        if result.get("exit") != 0:
            raise ScreenSetupError(
                "Forwarded skill reload failed: " + str(result.get("stderr") or "")
            )

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
        ``claude`` with our hooks (the device-side launcher creates its home/work dirs
        and wires the hook forwarder). A reused screen is RE-ASSERTED, not trusted:
        the hub's registry can outrun what the connector knows (a restarted
        connector has forgotten every sid until a create makes it re-adopt the
        tmux session that outlived it; a create sent on a dying transport was
        never delivered at all), and the cli silently drops ``rpc.call`` for a sid
        it does not know — so a turn that trusted the registry alone died in a blank
        3×60s prompt timeout whenever the two had diverged. The adopt-create is
        idempotent on the device: a live session keeps running, and keeps the
        system prompt it launched with (the launcher only reads it at screen
        creation); a lost one is respawned under the same sid + screen token.

        But adopt-create only respawns a screen the CONNECTOR forgot (it restarted);
        it cannot respawn one whose `claude` died while the connector kept running,
        because the connector still holds the sid and merely hot-reloads into the
        dead pane. So a reused screen is first probed for a live `claude`
        (``confirm_alive``); an explicitly dead one is closed and reopened under a
        fresh sid the connector must Spawn, rather than reasserted into a corpse."""
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
        if (env or {}).get("CHEESE_PRIVATE_CHAT") == "1":
            from app.domain.agent.private_chat import execution_target as private_target

            execution_target = private_target(
                project_id, topic_id, resource_id, device_id=device_id
            )
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
        stable_target = (
            {
                name: value
                for name, value in execution_target.items()
                if name != "context_tree"
            }
            if execution_target
            else None
        )
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
        # The home MUST be per topic, not per project: every hook event lands in
        # a spool under $HOME/.cheese, and the drainer ships that spool with the
        # hook URL + token in $HOME/.cheese/cheese-drain.env — which every screen
        # start overwrites (deliberately, so a rotated ticket reaches a long-lived
        # screen). With a project-shared home, all concurrent screens spool into
        # one dir and the drainer delivers everything to whichever session started
        # last: its topic swallows every screen's events while the other topics'
        # turns show zero output.
        home_dir = device_home_dir(project_id, resource_id)
        work_dir = self._work_dir(project_id, resource_id)
        api_base = await self._device_api_base(device_id)
        ca_pem = ""
        if settings.subscription_enabled:
            # Every request from every machine reached this way runs on the
            # subscription through the metering proxy — an enrolled device and a
            # leased Cloud box alike, since both are launched from right here
            # (#325 G2). This is what ``builds_model_env`` declares: the platform
            # sends the model choice, and the env around it is assembled below.
            # The machine holds no real credential either way: the env ships a
            # scoped cheese token as the fake login, the proxy verifies it and
            # injects the real token backend-side. The gateway route is NOT a
            # fallback here — falling back silently is exactly the model swap
            # this branch exists to kill (dev shipped device screens with
            # CLAUDE_MODEL=deepseek-chat while users thought they were talking
            # to Claude).
            ca_pem = _read_proxy_ca()
            # Session-length TTL, not the 1h default. This token is baked into the
            # bare process's HTTPS_PROXY (CONNECT credential) and
            # CLAUDE_CODE_OAUTH_TOKEN, both read ONCE at process start and never
            # hot-refreshed; the screen is reused across turns (a reassert only
            # re-attaches, it does not relaunch claude). A 1h token
            # thus expires under a still-running process, and every turn after the
            # first hour is rejected by the metering proxy (407) — the agent looks
            # dead. Same session lifetime as the CHEESE_TOKEN minted alongside it.
            session_token = mint_scoped_token(
                project_id=str(project_id),
                topic_id=str(topic_id),
                ttl_s=SESSION_TOKEN_TTL_S,
                remote_control=True,
                resource_id=str(resource_id),
                model=launch.model,
                # WHO acts with it. The launcher has known this all along and
                # let the minter fall back to a handle derived from the room —
                # which is the one thing a room cannot answer once it may seat
                # more than one agent.
                agent_handle=agent_handle,
            )
            tunnel_url = settings.subscription_tunnel_url.strip()
            via_tunnel = uses_tunnel(tunnel_url=tunnel_url)
            tunnel_port = tunnel_port_for_topic(topic_id)
            connect_proxy_url = connect_transport(
                session_token=session_token,
                via_tunnel=via_tunnel,
                tunnel_port=tunnel_port,
            )
            sub = provider_env.subscription_provider(
                ca_path=_DEVICE_PROXY_CA_PATH,
                project_id=str(project_id),
                topic_id=str(topic_id),
                session_token=session_token,
                connect_proxy_url=connect_proxy_url,
                no_proxy=self._no_proxy_hosts(),
            )
            # The subscription env WINS over the caller's `env` — that env is
            # the gateway shape (BASE_URL + model pins), and any of those keys
            # surviving flips the CLI into API-key mode or asks the subscription
            # for a model it does not serve. Dropped, not overridden, because
            # subscription_provider only ADDS keys.
            merged = {**(env or {})}
            for k in (
                "ANTHROPIC_BASE_URL",
                "CLAUDE_MODEL",
                "ANTHROPIC_DEFAULT_HAIKU_MODEL",
                "ANTHROPIC_DEFAULT_SONNET_MODEL",
                "ANTHROPIC_DEFAULT_OPUS_MODEL",
            ):
                merged.pop(k, None)
            merged.update(sub.env)
            if launch.model and not launch.model.startswith("claude-"):
                # Native auxiliary calls and subagents must use the selected
                # gateway model too; LiteLLM does not serve Claude aliases.
                for family in ("HAIKU", "SONNET", "OPUS"):
                    merged[f"ANTHROPIC_DEFAULT_{family}_MODEL"] = launch.model
            merged["CHEESE_REMOTE_CONTROL"] = "1"
            # The tunnel's CONNECT credential must carry the same place and RC
            # claims as the direct proxy URL; CHEESE_TOKEN authenticates hooks.
            merged["CHEESE_CONNECT_TOKEN"] = session_token
            # These scopes describe the Cheese control credential. The model
            # provider still authenticates inference at its existing proxy hop.
            merged["CLAUDE_CODE_OAUTH_SCOPES"] = (
                "user:inference user:profile user:sessions:claude_code"
            )
            if connect_proxy_url:
                # The meter accepts model hosts, not package registries.
                merged["CHEESE_MODEL_PROXY"] = "1"
            if via_tunnel:
                # Read by the launch script: it writes the helper and the token
                # file, and starts the helper before `claude`. Carried on the env
                # rather than as arguments because a remote machine's launch is
                # built entirely from `extra_env` — there is no other channel
                # into that builder.
                merged["CHEESE_TUNNEL_URL"] = tunnel_url
                merged["CHEESE_TUNNEL_PORT"] = str(tunnel_port)
            elif await self._device_ccproxy_upstream(device_id):
                # A device with its own ccproxy identity runs on the machine-ticket
                # model: the launcher's reconcile hands claude the device's
                # own ticket instead of our scoped token, and admission tells
                # the meter which identity to relay it over. Without this flag
                # such a device silently falls onto the platform-credential swap
                # path — the #393 dependency this model exists to remove.
                merged["CHEESE_MACHINE_TICKET"] = "1"
            model_env = merged
            # Preserve the birth expiry across device and backend restarts.
            credential_expires = _credential_expiry(session_token)
            model_env["CHEESE_TOKEN_EXPIRES"] = str(credential_expires)
        else:
            # No subscription deployed: the machine gets the backend's own model
            # route and its scoped token — never the upstream provider key. The
            # backend substitutes the project's virtual key, so the credential
            # stays on the box and spend is attributed without having to trust
            # the machine to report it.
            provider = provider_env.api_key_provider(
                gateway_base=f"{self._public_base}/llm",
                key=token,
                model=launch.model or settings.agent_model,
            )
            model_env = {**provider.env, **(env or {})}
            # Same stamp on the gateway path: the model credential is the scoped
            # `token`, and its expiry is what both the launcher and the reuse gate
            # check before adopting.
            credential_expires = _credential_expiry(token)
            model_env["CHEESE_TOKEN_EXPIRES"] = str(credential_expires)
        _warn_if_model_endpoint_is_box_local(model_env, device_id)
        # 运行环境预览's dial-out address. Derived from the base this machine already
        # reaches for hooks, git and the CLI rather than configured separately:
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
            # Every device owns its checkout and syncs through authenticated git.
            git_remote=f"{api_base}/projects/{project_id}/git",
            execution_target=execution_target,
            remote_control=model_env.get("CHEESE_REMOTE_CONTROL") == "1",
            ca_pem=ca_pem,
        )
        holes = launch.on(place)
        command, screen_env = machine_launcher.screen_launch(
            place,
            holes,
            hook_url=f"{api_base}/sandbox/hooks/{topic_id}",
            token=token,
        )
        screen_env.update(model_env)
        if execution_target is not None:
            # The assigned executor already owns the checkout and its environment.
            for name in (
                "CHEESE_GIT_REMOTE",
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
            execution_target=stable_target,
        )
        # The machine reports its environment back, so this is the one value
        # both sides of the seam can be asked for. Exported for every harness,
        # not only the ones with an executor: a screen adopted after a backend
        # restart is re-read from here, and one that came back without it read
        # as a configuration change on its next turn, every turn.
        screen_env["CHEESE_AGENT_CONFIG"] = configuration
        if existing is not None and existing.agent_configuration != configuration:
            # Called between turns, and only now: what a session was started
            # with is not fully known until the harness has been asked.
            await self._retire_screen(
                existing, topic_id=topic_id, reason="agent_configuration_changed"
            )
            existing = None
        mark("launcher_built")
        release_state = {} if existing is not None and execution_target else None
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
            alive, tunnel_down, _ = await asyncio.gather(
                self.confirm_alive(existing),
                self._tunnel_helper_is_down(existing),
                self._refresh_screen_files(
                    device_id,
                    home_dir,
                    release_state=release_state,
                    execution_token=execution_token,
                ),
            )
            if not alive or tunnel_down:
                # Adopt-create cannot restart a dead process or its tunnel while
                # the connector still knows the sid. A new sid runs the launcher,
                # which is why it is shipped only now.
                await self._retire_screen(
                    existing,
                    topic_id=topic_id,
                    reason="claude_not_alive" if not alive else "tunnel_helper_down",
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
            if release_state is not None:
                assert execution_target is not None
                released = await self._refresh_resident(
                    existing, home_dir, release_state
                )
                previous_tree = (existing.execution_target or {}).get(
                    "context_tree", {}
                )
                current_tree = execution_target.get("context_tree", {})
                if released or previous_tree.get("generation") != current_tree.get(
                    "generation"
                ):
                    await self._refresh_forwarded_context(
                        existing, home_dir, execution_target
                    )
        if existing is not None:
            # A reassert keeps the CURRENTLY-RUNNING `claude`, which still holds the
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
            hook_key=str(topic_id),
            env=screen_env,
        )
        mark("screen_opened")
        # Record what credential this freshly-Spawned `claude` was born with, so a
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

    async def precheck(
        self, project_id: uuid.UUID, topic_id: uuid.UUID
    ) -> tuple[str, int, str]:
        """Resolve the topic's pinned/online device + its agent identity BEFORE the
        base claims the topic's hook queue (pre-refactor ordering, review finding).
        The resolved tuple is handed back to ``ensure_ready`` via ``precheck``.
        Raises ``ScreenSetupError`` (offline pinned device, or none online)."""
        resolved = await self._resolve_device_agent(project_id, topic_id)
        if resolved is None:
            raise ScreenSetupError(
                "没有在线的绑定设备可运行本轮（self-hosted 设备未连接）"
            )
        return resolved

    async def ensure_ready(
        self,
        *,
        project_id: uuid.UUID,
        topic_id: uuid.UUID,
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
        assert isinstance(precheck, tuple)  # from our precheck
        device_id, agent_user_id, agent_handle = precheck
        if memory_scope == "personal":
            env = dict(
                env or {}, CHEESE_PRIVATE_CHAT="1", CHEESE_MEMORY_SCOPE="personal"
            )
            if owner:
                env["CHEESE_OWNER"] = owner
        prepares_environment = bool((env or {}).get("CHEESE_ENVIRONMENT")) and not (
            (env or {}).get("CHEESE_EXECUTION_TARGET")
            or (env or {}).get("CHEESE_PRIVATE_CHAT") == "1"
        )
        try:
            from app.core.db import async_session_factory

            factory = self._session_factory or async_session_factory
            async with factory() as room_session:
                room = await TopicService(room_session).lock_for_execution(topic_id)
                resource_id = room.resource_id or room.id
                env = {**(env or {}), "CHEESE_RESOURCE_ID": str(resource_id)}
                before = (
                    await environment_status(
                        self._hub, device_id, project_id, resource_id
                    )
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
                await room_session.commit()
            self._subscription_devices[topic_id] = device_id
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
                                    "环境已准备完成，但芝士启动后退出，请查看房间终端"
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

    async def send_interrupt(self, screen: HubScreen) -> bool:
        """Escape into the screen, down the channel that carries a watching
        person's keystrokes. Not the rendezvous socket: that enqueues a MESSAGE,
        and a message is what `send` is for — this is the key that takes the
        work away without saying anything."""
        await self._hub.viewer_input(screen.device_id, screen.sid, b"\x1b")
        return True

    async def stage_images(
        self, screen: HubScreen, images: list[dict]
    ) -> tuple[list[dict], list[dict]]:
        """Copy each uploaded image onto the machine this screen runs on.

        The upload landed in the backend's own worktree; a device is a different
        filesystem, so without this the prompt's `@uploads/x.png` points at
        nothing and 芝士 is handed a mention that resolves to no image.

        Per image, and never fatal. One that cannot be staged — the connector
        predates the file frame (this is real: the binary deployed on the dev
        box was built the day before `file.put` existed, so every frame was
        dropped unanswered and the send timed out), the machine is wedged, the
        bytes are gone — comes back in the second list and the turn says so.
        Before this, the failure propagated out of `send_prompt` and the whole
        message vanished silently.
        """
        if screen.project_id is None or screen.topic_id is None:
            # A screen adopted without its coordinates cannot be told which
            # worktree the file came from. Say so rather than send a mention
            # that resolves to nothing.
            return [], list(images)
        staged: list[dict] = []
        lost: list[dict] = []
        for image in images:
            path = str(image.get("path") or "")
            try:
                data = ws.read_attachment(screen.project_id, screen.topic_id, path)
                await self._hub.put_file(
                    screen.device_id,
                    screen.sid,
                    path,
                    data,
                    timeout=_FILE_STAGE_TIMEOUT_S,
                )
                if screen.execution_target:
                    from app.domain.agent import private_chat

                    target = screen.execution_target
                    if target:
                        await private_chat.control(
                            target,
                            {
                                "subtype": "stage_file",
                                "path": path,
                                "data": base64.b64encode(data).decode(),
                            },
                            hub=self._hub,
                        )
            except Exception as exc:  # noqa: BLE001 — an image is not the message
                # `str(exc)` is EMPTY for the failure this actually hits — a bare
                # `TimeoutError` from a connector too old to know `file.put`, which
                # drops the frame without answering. Naming the type is the whole
                # difference between a line that ends in a colon and one that says
                # the send timed out.
                logger.warning(
                    "could not stage image %s onto device %s (topic=%s): %s",
                    path,
                    screen.device_id,
                    screen.topic_id,
                    str(exc) or exc.__class__.__name__,
                )
                lost.append(image)
            else:
                staged.append(image)
        return staged, lost

    async def send_prompt(self, screen: HubScreen, prompt: str) -> bool | None:
        """Deliver the prompt over the screen's rendezvous socket, where Claude
        Code enqueues it as `origin: {kind:"human"}` — the same place a keystroke
        lands, with none of a keystroke's blindness.

        A failure here is a real failure: the connector answers with an error
        when the socket never bound, the token never appeared, or the session
        refused the frame — instead of a driver silently re-pasting into a
        composer nobody was reading (2026-08-16)."""
        try:
            call_id = await self._hub.call_screen(
                screen.device_id, screen.sid, "prompt", [prompt]
            )
            result = await self._hub.await_call(
                screen.device_id, call_id, timeout=_PROMPT_DELIVERY_TIMEOUT_S
            )
        except Exception as exc:  # noqa: BLE001 — a failed prompt ends the turn
            # NOT "启动失败": by this point the screen is up. Saying what actually
            # failed is the difference between someone re-@ing the agent and
            # someone going to look at the machine.
            raise ScreenSetupError(
                f"提示词没能送进机器上的会话：{str(exc) or exc.__class__.__name__}"
            ) from exc
        if isinstance(result, dict) and isinstance(result.get("ready"), bool):
            return result["ready"]
        return None

    def _credential_is_stale(self, screen: HubScreen) -> bool:
        """Whether the credential this screen's `claude` was LAUNCHED with has
        expired, or is within the retire margin of it (#388 缺陷二).

        The freshness of that credential is part of whether a screen may be REUSED,
        not just whether its process is alive: `claude` reads its model credential
        (HTTPS_PROXY CONNECT password / CLAUDE_CODE_OAUTH_TOKEN) exactly once at
        startup, and a reused screen is only reasserted (an adopt-create),
        never relaunched — so a still-running process on a dead credential is
        rejected on every request while the process-tree probe (`confirm_alive`)
        keeps reporting it healthy. This is the local, in-memory half of the gate;
        it never touches the device.

        Conservative in the same direction as `confirm_alive`: a screen with no
        recorded expiry (`None` — adopted after a server restart, or a dev token
        with no decodable claim) is treated as FRESH and never retired on missing
        information, so we only ever retire a credential we can prove is dying."""
        exp = screen.credential_expires
        if exp is None:
            return False
        return exp <= int(time.time()) + _CREDENTIAL_EXPIRY_MARGIN_S

    async def _tunnel_helper_is_down(self, screen: HubScreen) -> bool:
        """Whether the machine-local tunnel helper this screen's `claude` dials has
        stopped listening — the second half of the reuse gate, alongside
        `_credential_is_stale`.

        Both answer the same question about different dependencies: `claude` reads
        its HTTPS_PROXY exactly once at startup, and a reused screen is reasserted
        rather than relaunched, so a dependency that dies under the running process
        can never be repaired in place. For the credential that meant a permanent
        407; for the tunnel helper it means a permanent ConnectionRefused, with the
        process-tree probe reporting `alive` throughout.

        Skipped entirely on a deployment with no tunnel (the device dials the meter
        directly, so there is no helper to lose) — that keeps the per-turn cost at
        zero everywhere the failure cannot happen.

        Conservative in the same direction as `confirm_alive`: only an explicit
        `down` retires a screen. An exec failure, a non-zero exit, or an `unknown`
        (no /proc, no awk) is read as "still up", so a probe hiccup never throws
        away a healthy screen and its in-progress work."""
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
                env={"CHEESE_TUNNEL_PROBE_PORT": str(tunnel_port_for_topic(topic_id))},
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

    async def confirm_alive(self, screen: HubScreen) -> bool:
        """Idle-suspect liveness probe for a device screen (turn 活跃度检测, the
        device half). Once a turn crosses `idle_suspect_s`, the hooks substrate
        calls this to tell a `claude` that is silently working — a long FOREGROUND
        command (pytest, a build) emits NO interim hook, so a hook-silent window is
        indistinguishable from a wedge on hooks alone — from one whose process
        actually died.

        Asks the device directly over the hub's `exec` (DEVICE_ALIVE_PROBE): is a
        live `claude` still running for THIS topic on the box? The process-tree
        signal is the one that stays valid through a hook-silent window; there is no
        cheap screen-byte signal on a headless device (the hub relays raw bytes only
        to a live browser viewer). Every device uses the same probe.

        Conservative: only an explicit
        `dead` reading ends the turn; any exec failure/timeout, a non-zero exit, or
        an `unknown`/empty result is read as alive, so a link hiccup or a hardened
        /proc never false-kills a turn that is really still working."""
        topic_id = screen.topic_id
        if topic_id is None:
            return True
        started = time.monotonic()
        try:
            result = await self._hub.exec(
                screen.device_id,
                ["sh", "-c", DEVICE_ALIVE_PROBE],
                env={"CHEESE_ALIVE_TOPIC": str(topic_id)},
                timeout=_ALIVE_PROBE_TIMEOUT_S,
            )
        except Exception:  # noqa: BLE001 — a probe failure is not proof of death
            return True
        finally:
            logger.info(
                "device_probe_timing topic=%s probe=alive elapsed_ms=%.3f",
                topic_id,
                (time.monotonic() - started) * 1000,
            )
        if result.get("exit") != 0:
            return True
        return (result.get("stdout") or "").strip() != "dead"


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
