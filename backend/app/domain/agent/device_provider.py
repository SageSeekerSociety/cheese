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
import hashlib
import json
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import settings
from app.core.sandbox_auth import mint_scoped_token, scoped_token_claims
from app.domain.agent import provider_env
from app.domain.agent.device_hub import (
    DeviceHub,
    DeviceOffline,
    HubScreen,
    device_hub,
)
from app.domain.agent.harness.claude_code import (
    DEVICE_ALIVE_PROBE,
    DEVICE_TUNNEL_PROBE,
    SESSION_TOKEN_TTL_S,
    Channel,
    ScreenSetupError,
    build_screen_launch,
    drop_topic_subscriptions,
)
from app.domain.agent.harness.launch import LaunchPlan
from app.domain.agent.platform_failures import (
    DEVICE_OFFLINE_MESSAGE,
    HOST_UNREACHABLE_CODE,
)
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


def _git_author(project_id: uuid.UUID, topic_id: uuid.UUID) -> tuple[str, str] | None:
    """Who this topic's commits belong to, for a machine that owns its own tree
    and commits with plain git. None → the launcher's 芝士 default."""
    from app.domain.workspace import identity as ws_identity

    found = ws_identity.read(project_id, topic_id)
    return None if found is None else (found.name, found.email)


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
# seconds of expiry, mirroring the launcher's ``$EXPFILE`` gate (device_launch)
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
    """The UNIX expiry the device screen stamps for the model credential it is
    launched with (``CHEESE_TOKEN_EXPIRES``). The launcher records it against the
    inner tmux session it creates, and a later launch reads it back to tell a
    session whose baked credential has DIED — a bare `claude` reads its OAUTH /
    proxy credential ONCE at startup and never re-reads it, so a freshly minted
    token never reaches an already-running (adopted) process — from one still
    holding a good token, and retires only the former. A token with no decodable
    claim (a dev ``SANDBOX_TOKEN`` passthrough) falls back to a session length from
    now, so the launcher never reads it as perpetually stale and churns the screen
    every turn."""
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


def device_home_dir(project_id: uuid.UUID, place_id: uuid.UUID) -> str:
    return f"{DEVICE_HOME_ROOT}/{project_id}/{place_id}"


async def environment_status(
    hub: DeviceHub,
    device_id: str,
    project_id: uuid.UUID,
    topic_id: uuid.UUID,
    *,
    action: str = "status",
) -> dict:
    home = device_home_dir(project_id, topic_id)
    reset_marker = (
        'mkdir -p "$HOME/.claude"; touch "$HOME/.claude/environment-restart"; '
        if action == "reset"
        else ""
    )
    result = await hub.exec(
        device_id,
        [
            "sh",
            "-c",
            f'export HOME="{home}"; '
            'if [ -f "$HOME/.claude/cheese-environment.py" ]; then '
            f'python3 "$HOME/.claude/cheese-environment.py" {action} || exit $?; '
            "else printf '%s' '{\"state\":\"pending\"}'; fi; " + reset_marker,
        ],
        timeout=10,
    )
    if result.get("exit") != 0:
        raise ScreenSetupError("无法读取机器上的环境准备状态")
    return json.loads(result.get("stdout") or '{"state":"pending"}')


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
        """Topics durably pinned to currently connected devices OF THIS CHANNEL.

        No screen comes back with them: the ``HubScreen`` that was open before
        the restart is gone from this process, and the device reattaches on the
        topic's next turn. What survives is the PIN, which is enough to start
        listening again.

        A topic may come back from exactly ONE channel, because a topic's hooks
        arrive on exactly one process-wide queue (``hook_router``) and every
        channel that discovers a topic puts a consumer on it. Two consumers do
        not each get a copy — they SPLIT the queue: a message that arrives in
        four flushes is assembled half by each, both halves land as separate
        replies, and the Stop that carries the full text lands a third, because
        "已经说过的话" is a per-consumer list. That is what an unfiltered
        discover cost: every topic on an online connector was recovered by the
        device channel AND the cloud one, on every backend start and every
        connector reconnect.
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
        return [
            (project_id, topic_id, None, None) for project_id, topic_id, _ in scopes
        ]

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

    def _existing_screen(self, device_id: str, topic_id: uuid.UUID) -> HubScreen | None:
        for screen in self._hub.all_online_screens():
            if screen.device_id == device_id and screen.topic_id == topic_id:
                return screen
        return None

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
        return f"$HOME/.cheese/work/{project_id}/{topic_id}"

    def _hook_url(self, topic_id: uuid.UUID) -> str:
        # Reuse the existing sandbox hook endpoint (scoped-token auth + shared
        # hook_router), so the device path adds no second hook surface.
        return f"{self._public_base}/sandbox/hooks/{topic_id}"

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
        self, device_id: str, topic_id: uuid.UUID, command: list[str]
    ) -> list[str]:
        """Write the launch script to a FILE on the device (over the link's one-shot
        ``exec``, script on stdin) and return a short command that runs it.

        The launcher cannot ride in argv: the frozen cli hands the command to
        ``tmux new-session``, and tmux's client→server imsg buffer caps the whole
        packed command around 16KB — beyond it new-session dies with ``command too
        long``. Since #308 embedded the assembled system prompt in the script, every
        real launch is tens of KB, so argv delivery broke every device spawn (and
        the failure was invisible: session.error is fire-and-forget and the prompt
        just timed out). The file path is per-topic and rewritten before every
        create/re-assert, so a respawn always runs the current turn's script."""
        assert command[:2] == ["bash", "-lc"] and len(command) == 3
        script = command[2]
        path = f"$HOME/.cheese/launch/{topic_id}.sh"
        started = time.monotonic()
        try:
            result = await self._hub.exec(
                device_id,
                ["sh", "-c", f'mkdir -p "$HOME/.cheese/launch" && cat > "{path}"'],
                stdin=script,
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
            "launcher shipped to device %s in %.1fs (topic=%s, %d bytes)",
            device_id,
            time.monotonic() - started,
            topic_id,
            len(script),
        )
        if result.get("exit") != 0:
            raise ScreenSetupError(
                f"无法把启动脚本写到设备上：{result.get('stderr') or result}"
            )
        return ["bash", "-lc", f'exec bash "{path}"']

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
        launch: LaunchPlan,
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
        existing = self._existing_screen(device_id, topic_id)
        if existing is not None and (env or {}).get("CHEESE_ENVIRONMENT"):
            status = await environment_status(
                self._hub, device_id, project_id, topic_id
            )
            if status["state"] == "preparing":
                return existing
        configuration = (env or {}).get("CHEESE_AGENT_CONFIG", "")
        if (
            existing is not None
            and configuration
            and existing.agent_configuration != configuration
        ):
            # Called between turns. A running CLI cannot adopt a changed model or role.
            await self._hub.close_screen(existing.device_id, existing.sid)
            existing = None
        if existing is not None and self._credential_is_stale(existing):
            # #388 缺陷二: the screen is still alive, but the credential its `claude`
            # was LAUNCHED with has expired (or is within the retire margin). That
            # credential is read ONCE at startup and never re-read, and a reused
            # screen is only reasserted (an adopt-create), never relaunched —
            # so reasserting here would leave the process forever holding a dead
            # token, 407'd by the metering proxy / 401'd upstream on every turn
            # while its process stays healthy (the exact "alive process + dead
            # credential = looks healthy to the probe" the issue names). Retire it:
            # close_screen makes the connector forget the sid, so the OPEN below
            # Spawns a fresh `claude` carrying THIS launch's live credential. This
            # is the same retirement the launcher's `$EXPFILE` gate does in its
            # CREATE branch — but that branch only runs when the connector already
            # forgot the sid, so on plain reuse this backend-side gate is the ONLY
            # place it can fire. A refreshed host credential is thereby picked up on
            # the next summon instead of an unrunnable screen being reused forever.
            await self._hub.close_screen(existing.device_id, existing.sid)
            existing = None
        if existing is not None and not await self.confirm_alive(existing):
            # The hub still has a screen for this topic, but the `claude` behind it
            # is GONE — its tmux session was killed out from under a STILL-RUNNING
            # connector (an orphan sweep, a `tmux kill-server`, a crash). Reasserting
            # (adopt-create, #369) does NOT bring it back: the frozen connector,
            # finding the sid still in its own in-memory session map, only
            # re-attaches and returns — it re-Spawns the launcher ONLY
            # for a sid it has forgotten, i.e. after IT restarted (cli host.go
            # createSession). #369 rebuilds a screen a CONNECTOR restart lost; it
            # cannot rebuild one whose `claude` died while the connector lived. The
            # turn would then prompt a dead pane and die in the 25s
            # "会话没有任何反应" delivery timeout, reaching no model — which on a
            # subscription deployment silently never
            # bills a turn (#325 G2). Drop the stale screen (session.close makes the
            # connector forget the sid too) so the code below OPENS a fresh one under
            # a NEW sid the connector cannot short-circuit and must Spawn: the
            # launcher runs, `claude` restarts, hooks flow. Only an explicit `dead`
            # reading forces this (see `confirm_alive`) — an alive, `unknown`, or
            # probe-hiccup screen is still reasserted, exactly as before.
            await self._hub.close_screen(existing.device_id, existing.sid)
            existing = None
        if existing is not None and await self._tunnel_helper_is_down(existing):
            # The third way a reused screen can be alive and unusable, and the one
            # that had no gate: its `claude` runs, its credential is fresh, and the
            # machine-local tunnel helper its HTTPS_PROXY points at is GONE. That
            # helper is started ONLY by `cheese-tunnel-up`, which runs ONLY as the
            # launcher's prefix — and reuse reasserts (an adopt-create)
            # instead of relaunching, so nothing on either side ever restarts it.
            # `claude` read that HTTPS_PROXY once at startup and never re-reads it,
            # so every turn from then on dies with `API Error: Unable to connect to
            # API (ConnectionRefused)` while `confirm_alive` keeps answering
            # `alive` — the same "live process + dead dependency" shape the
            # credential gate above exists for, on the other dependency.
            #
            # Measured 2026-08-18: the dev box's standing data plane was swapped
            # (#573) under five still-running screens. Every subsequent turn failed,
            # one topic replayed the same 28-message batch 30 times at ~3 minutes a
            # try, and no re-@ could ever have fixed it — the only cure was a fresh
            # launch, which nothing was able to ask for. Retire the screen here so
            # the OPEN below Spawns one whose launcher runs `cheese-tunnel-up`
            # again.
            await self._hub.close_screen(existing.device_id, existing.sid)
            existing = None
        # Device-side paths (the launcher mkdir -p's them). Kept under a stable per
        # project/topic root so the screen's git-backed work persists across turns.
        # The home MUST be per topic, not per project: every hook event lands in
        # a spool under $HOME/.claude, and the drainer ships that spool with the
        # hook URL + token in $HOME/.claude/cheese-drain.env — which every screen
        # start overwrites (deliberately, so a rotated ticket reaches a long-lived
        # screen). With a project-shared home, all concurrent screens spool into
        # one dir and the drainer delivers everything to whichever session started
        # last: its topic swallows every screen's events while the other topics'
        # turns show zero output.
        home_dir = device_home_dir(project_id, topic_id)
        work_dir = self._work_dir(project_id, topic_id)
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
            # Stamp the minted session token's expiry so the launcher can retire an
            # inner tmux session whose baked credential has died instead of adopting
            # it (device_launch: the reuse that outlives a TTL bump — #385), and so
            # the backend's own reuse gate below can do the same for a plain reuse.
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
        model_env["CHEESE_PREVIEW_URL"] = _preview_ws_url(self._public_base)
        command, screen_env = build_screen_launch(
            hook_url=self._hook_url(topic_id),
            hook_token=token,
            home_dir=home_dir,
            work_dir=work_dir,
            model=launch.model,
            # The third thing a plan carries, and the one this channel used to
            # drop on the floor. A screen is retired and reopened for reasons
            # that say nothing about the conversation (the three gates above),
            # and until this was passed on, every one of them started the topic's
            # agent from a blank slate — the room's memory of its own turns
            # ending at whichever gate last fired.
            resume_session_id=launch.resume_session_id,
            extra_env=model_env,
            # The base already maps 1:1 onto the backend root (see
            # settings.connector_public_base), and every backend route is bare
            # since #370 step 2 — so the CLI's base IS that base. Appending
            # another `/api` was right only while the 2.0 routes carried their
            # own prefix; afterwards it injected `<origin>/api/api` and every
            # `cheese` command in a device sandbox 404'd with 话题不存在.
            api_base=self._public_base,
            project_id=str(project_id),
            topic_id=str(topic_id),
            author=agent_handle,
            git_author=_git_author(project_id, topic_id),
            # Every device owns its checkout and syncs through authenticated git.
            git_remote=f"{self._public_base}/projects/{project_id}/git",
            git_branch=ws.branch_for_tree(ws.tree_for_place(topic_id)),
            system_prompt=launch.system_prompt,
            ca_pem=ca_pem,
        )
        command = await self._ship_launcher(device_id, topic_id, command)
        if existing is not None:
            # A reassert keeps the CURRENTLY-RUNNING `claude`, which still holds the
            # credential it was born with — so the recorded birth expiry must NOT be
            # overwritten with this launch's freshly-minted one (the new token never
            # reaches the running process). It stays as the reuse gate's truth.
            await self._hub.reassert_screen(existing, command=command, env=screen_env)
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
        # Record what credential this freshly-Spawned `claude` was born with, so a
        # later turn's reuse gate (and the zero-output fuse) can tell a live
        # credential from a dead one without re-deriving it.
        screen.credential_expires = credential_expires
        screen.agent_configuration = configuration
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
        launch: LaunchPlan,
        precheck: object,
    ) -> HubScreen:
        """Reuse/open the topic's screen running `claude` with our hooks on the
        device resolved by ``precheck``; return the screen (ctx). Raises
        ScreenSetupError when the screen fails.

        This channel READS the plan rather than performing it — a remote screen
        is built out of a shell script this side writes, so the launch has to be
        assembled here, and the script it goes into says ``claude``. That is the
        crossing the ledger still records against this file: the tmux channel
        can host whatever it is handed, and this one cannot."""
        assert isinstance(precheck, tuple)  # from our precheck
        device_id, agent_user_id, agent_handle = precheck
        try:
            before = (
                await environment_status(self._hub, device_id, project_id, topic_id)
                if (env or {}).get("CHEESE_ENVIRONMENT")
                else {}
            )
            prior_screen = self._existing_screen(device_id, topic_id)
            screen = await self._ensure_screen(
                device_id=device_id,
                agent_user_id=agent_user_id,
                agent_handle=agent_handle,
                project_id=project_id,
                topic_id=topic_id,
                token=token,
                env=env,
                launch=launch,
            )
            self._subscription_devices[topic_id] = device_id
            if (env or {}).get("CHEESE_ENVIRONMENT"):
                # A process started before this feature keeps its environment
                # until its next restart; it has no preparation receipt yet.
                if before.get("state") == "pending" and screen is prior_screen:
                    return screen
                try:
                    polling_started = time.monotonic()
                    start_deadline = polling_started + 60
                    async with asyncio.timeout(3660):
                        while True:
                            status = await environment_status(
                                self._hub, device_id, project_id, topic_id
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
                data = ws.read_file_bytes(
                    screen.project_id, path, topic_id=screen.topic_id
                )
                await self._hub.put_file(
                    screen.device_id,
                    screen.sid,
                    path,
                    data,
                    timeout=_FILE_STAGE_TIMEOUT_S,
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
        try:
            result = await self._hub.exec(
                screen.device_id,
                ["sh", "-c", DEVICE_TUNNEL_PROBE],
                env={"CHEESE_TUNNEL_PROBE_PORT": str(tunnel_port_for_topic(topic_id))},
                timeout=_ALIVE_PROBE_TIMEOUT_S,
            )
        except Exception:  # noqa: BLE001 — a probe failure is not proof of death
            return False
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
        try:
            result = await self._hub.exec(
                screen.device_id,
                ["sh", "-c", DEVICE_ALIVE_PROBE],
                env={"CHEESE_ALIVE_TOPIC": str(topic_id)},
                timeout=_ALIVE_PROBE_TIMEOUT_S,
            )
        except Exception:  # noqa: BLE001 — a probe failure is not proof of death
            return True
        if result.get("exit") != 0:
            return True
        return (result.get("stdout") or "").strip() != "dead"

    # --- teardown ----------------------------------------------------------

    async def release_topic(self, project_id: uuid.UUID, topic_id: uuid.UUID) -> None:
        """Free a done topic's screen — the device backend's teardown, symmetric to
        the local backend's ``ws.stop_topic_container`` and to this provider's own
        ``checkpoint``. Called when a topic is accepted/archived or reaped for being
        idle; without it the screen (and the ``claude`` process + tmux session behind
        it) leaks on the machine forever, since the reaper only ever knew how to free
        Docker containers.

        The device's clone under its per-topic work dir is removed too. The
        per-topic home keeps session state and is deliberately not part of this
        worktree cleanup.

        Best-effort and idempotent: no screen (device offline / already gone) is a
        successful no-op, and every failure is swallowed so one topic can never break
        a reap loop. The screen is forgotten even when its device is offline, so an
        archived topic leaves no stale registry entry behind."""
        await drop_topic_subscriptions(topic_id)
        for screen in self._hub.screens_for_topic(topic_id):
            device_id = screen.device_id
            try:
                # Close first so the device's claude process stops holding the tree,
                # THEN remove the (now idle) device clone. close_screen forgets the
                # screen even for an offline device (its session_close is a no-op),
                # so our registry never leaks an archived topic.
                await self._hub.close_screen(device_id, screen.sid)
                await self._remove_work_dir(device_id, project_id, topic_id)
            except Exception:  # noqa: BLE001 — one screen must not stop the rest
                logger.warning(
                    "release_topic: failed freeing screen %s on device %s (topic %s)",
                    screen.sid,
                    device_id,
                    topic_id,
                    exc_info=True,
                )

    async def _remove_work_dir(
        self, device_id: str, project_id: uuid.UUID, topic_id: uuid.UUID
    ) -> None:
        """Remove this topic's checkout from an online device."""
        if not self._hub.is_online(device_id):
            return
        work_dir = self._work_dir(project_id, topic_id)
        # `$HOME` in the scratch path is expanded by the device's shell; project and
        # topic are UUIDs (no shell metacharacters), so the argv stays a fixed
        # boundary with nothing to inject.
        await self._hub.exec(
            device_id,
            ["sh", "-lc", f'rm -rf -- "{work_dir}"'],
            timeout=30,
        )


async def release_topic_screen(
    project_id: uuid.UUID,
    topic_id: uuid.UUID,
    *,
    hub: DeviceHub | None = None,
    session_factory: async_sessionmaker | None = None,
) -> None:
    """Free a topic's device screen from a caller that holds no ``DeviceChannel`` —
    the accept/archive path and the idle reaper both reach compute through ws-level
    helpers, not the compute pool. Thin wrapper over ``DeviceChannel.release_topic``
    bound to the shared ``device_hub`` singleton (``hub=None``). Best-effort and
    idempotent, so it is safe to call for EVERY archived/idle topic regardless of
    backend: a topic that never ran on a device simply has no screen to free."""
    channel = DeviceChannel(hub=hub, session_factory=session_factory)
    await channel.release_topic(project_id, topic_id)


async def list_device_homes(
    device_id: str, *, hub: DeviceHub | None = None
) -> list[tuple[str, str]]:
    """Every ``(project, place)`` directory pair under the device's home root,
    as the device's shell sees them — names only, nothing resolved.

    Raises ``DeviceOffline`` like ``exec`` does; the caller decides what an
    unreachable device means for its sweep. Lists with a shell loop rather than
    `find -printf`, which is GNU-only and a device may be a Mac."""
    hub = hub or device_hub
    script = (
        f'cd "{DEVICE_HOME_ROOT}" 2>/dev/null || exit 0; '
        'for p in */*; do [ -d "$p" ] && printf "%s\\n" "$p"; done'
    )
    result = await hub.exec(device_id, ["sh", "-lc", script], timeout=30)
    pairs: list[tuple[str, str]] = []
    for line in str(result.get("stdout") or "").splitlines():
        project, sep, place = line.strip().partition("/")
        if sep and project and place:
            pairs.append((project, place))
    return pairs


async def remove_device_home(
    device_id: str,
    project_id: uuid.UUID,
    place_id: uuid.UUID,
    *,
    hub: DeviceHub | None = None,
) -> None:
    """Delete one place's isolated home on the device. Raises ``DeviceOffline``
    when there is no link, and ``RuntimeError`` when the device ran the removal
    and reported it failed — a home that is still there must not be logged as
    gone."""
    hub = hub or device_hub
    home = device_home_dir(project_id, place_id)
    # `$HOME` is expanded by the device's shell; both ids are UUIDs (no shell
    # metacharacters), so the argv is a fixed boundary with nothing to inject.
    # A home is 1-4 GB of session files and caches, hence the long timeout.
    result = await hub.exec(
        device_id, ["sh", "-lc", f'rm -rf -- "{home}"'], timeout=300
    )
    if result.get("exit") != 0:
        raise RuntimeError(
            f"rm -rf {home} exited {result.get('exit')}: "
            f"{str(result.get('stderr') or '').strip()}"
        )


# What a home holds that the platform keeps when the home goes: claude's session
# files and the todo lists beside them. Caches and the rest are rebuilt.
TRANSCRIPT_DIRS = (".claude/projects", ".claude/todos")
# Left in the home by a successful upload. Its mtime is the moment that upload
# STARTED, so a session file written any later is newer than it and a later run
# uploads again; an unchanged home is recognised with one POSIX `find -newer`,
# on the device's own clock, so the platform's clock never enters into it.
TRANSCRIPT_MARK = ".transcripts-uploaded"
TRANSCRIPT_OUTCOMES = ("none", "unchanged", "uploaded")
# tar+gzip of a few GB of session files, then the upload over whatever uplink
# the machine has, with `transcripts_max_bytes` (512 MB) as the ceiling on the
# bytes: fifteen minutes is generous for that and still bounds the archive
# request this runs under.
TRANSCRIPT_UPLOAD_TIMEOUT_S = 900


def transcript_upload_script(project_id: uuid.UUID, place_id: uuid.UUID) -> str:
    """The shell that ships one home's transcripts to the platform.

    Prints its outcome as the last line — `none` (the home never ran a
    session, nothing to keep), `unchanged` (already stored, nothing new since)
    or `uploaded` — and exits non-zero on any failure, with curl's reason on
    stderr. The platform's address and this machine's credential come from
    `CHEESE_API` / `CHEESE_TOKEN`, the cli's own env override names
    (cli/internal/apicli), handed to the command by the exec frame.

    Streams with `-T -` rather than `--data-binary @-`: the latter reads the
    whole archive into memory before sending, and a home can be gigabytes.
    Only curl's exit decides the pipeline's (no `pipefail` in POSIX sh), which
    is enough: a tar that died leaves a truncated stream, and the platform
    refuses that with a 4xx that `-f` turns into a failure."""
    home = device_home_dir(project_id, place_id)
    projects, todos = TRANSCRIPT_DIRS
    return (
        f'home="{home}"; mark="$home/{TRANSCRIPT_MARK}"\n'
        f'[ -d "$home/{projects}" ] || {{ echo none; exit 0; }}\n'
        f"set -- {projects}\n"
        f'[ -d "$home/{todos}" ] && set -- "$@" {todos}\n'
        'if [ -e "$mark" ] && [ -z "$(cd "$home" && find "$@" -type f '
        '-newer "$mark" | head -n 1)" ]; then echo unchanged; exit 0; fi\n'
        'cd "$home" || exit 1\n'
        'touch "$mark.pending"\n'
        # macOS tar otherwise packs an AppleDouble `._x` twin beside every file
        # that carries extended attributes — bytes the platform has no use for.
        'COPYFILE_DISABLE=1 tar czf - "$@" 2>/dev/null | curl -sS -f -T - '
        '-H "Authorization: Bearer $CHEESE_TOKEN" '
        f'"$CHEESE_API/transcripts/{project_id}/{place_id}" '
        '&& mv -f "$mark.pending" "$mark" && echo && echo uploaded\n'
    )


async def upload_device_transcripts(
    device_id: str,
    project_id: uuid.UUID,
    place_id: uuid.UUID,
    *,
    token: str,
    hub: DeviceHub | None = None,
) -> tuple[str, str]:
    """Have the device store one home's transcripts on the platform, before the
    home is removed. Returns ``(outcome, receipt)``: the outcome is one of
    ``TRANSCRIPT_OUTCOMES``, the receipt is the platform's answer (size and
    sha256) on an upload and empty otherwise. Raises ``DeviceOffline`` like
    ``exec`` does and ``RuntimeError`` when the device ran the upload and it
    did not go through — the caller must then leave the home alone."""
    hub = hub or device_hub
    # The same base the machine's cli dials and its hooks post to, so it is
    # reachable from there by construction.
    api_base = f"{settings.connector_public_base.rstrip('/')}/connector"
    result = await hub.exec(
        device_id,
        ["sh", "-lc", transcript_upload_script(project_id, place_id)],
        env={"CHEESE_API": api_base, "CHEESE_TOKEN": token},
        timeout=TRANSCRIPT_UPLOAD_TIMEOUT_S,
    )
    lines = [
        line.strip()
        for line in str(result.get("stdout") or "").splitlines()
        if line.strip()
    ]
    outcome = lines[-1] if lines else ""
    if result.get("exit") != 0 or outcome not in TRANSCRIPT_OUTCOMES:
        detail = str(result.get("stderr") or "").strip() or " ".join(lines[-2:])
        raise RuntimeError(
            f"transcript upload exited {result.get('exit')}: {detail or 'no output'}"
        )
    receipt = lines[-2] if outcome == "uploaded" and len(lines) > 1 else ""
    return outcome, receipt


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
