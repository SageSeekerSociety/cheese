"""DeviceProvider — the self-hosted / BYO-compute backend (AGENT_BACKEND=device, P3).

Symmetric to ``TmuxHooksProvider`` but the interactive ``claude`` runs on a *user's
own enrolled machine* instead of a platform container. The platform opens a screen on
the device over the frozen ``link.Msg`` channel (``DeviceHub``); the screen runs
``claude`` with our hooks (``device_launch``), so structured events come back through
the SAME Claude Code hook path (``/sandbox/hooks/{topic}`` → ``hook_router`` →
``translate_hook``) the tmux backend proved. The prompt is delivered by the minimal
cheeselet's ``prompt`` function — not by reading/writing the screen from the backend.

Per turn (``run_turn``):
  1. resolve an online device bound to the project + its agent identity (DB),
  2. ensure a screen for the topic on that device (open via ``DeviceHub`` if absent),
  3. register the topic's hook queue, then call the cheeselet ``prompt`` with the turn,
  4. drain the hook queue, translating each hook to an ``AgentEvent`` (reused verbatim),
  5. on the ``Stop`` hook (→ ``AgentResult``) end the turn stream.

``checkpoint`` snapshots the topic worktree when the device is CO-LOCATED (it edited
the backend's real tree); for a remote device it is a no-op (the device owns its own
tree and pushes it back over git smart-HTTP instead).
"""

import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import settings
from app.core.sandbox_auth import mint_scoped_token, scoped_token_claims
from app.domain.agent import awaited_tasks, provider_env
from app.domain.agent.device_hub import DeviceHub, HubScreen, device_hub
from app.domain.agent.device_launch import DEVICE_ALIVE_PROBE, build_screen_launch
from app.domain.agent.hook_events import HookRouter
from app.domain.agent.hooks_substrate import (
    SESSION_TOKEN_TTL_S,
    HooksTurnProvider,
    ScreenSetupError,
)
from app.domain.agent.platform_failures import DEVICE_OFFLINE_MESSAGE
from app.domain.device.service import DeviceService
from app.domain.device.supply import Supply, has_runnable_transport
from app.domain.device.wiring import sql_device_service
from app.domain.identity.services import IdentityService
from app.domain.workspace import service as ws

# Resolve the device a turn runs on for (project, topic) → (device_id, agent_user_id,
# agent_handle). Takes both ids because the device is chosen with topic affinity, not
# just per project (execution-architecture v4 §affinity).
logger = logging.getLogger(__name__)

DeviceResolver = Callable[
    [uuid.UUID, uuid.UUID], Awaitable["tuple[str, int, str] | None"]
]

# #358 · what a turn gets when its only/pinned machine is enrolled as the boxed
# `isolated` 档: a clean, actionable refusal, NOT a silent bare-on-host launch. It
# is deliberately NOT one of platform_failures' host-scoped classifications — an
# `isolated` machine is not unhealthy, so this must never quarantine it; it is a
# 「该档尚未实现」 turn error the owner resolves by opting the machine into
# whole-machine (Hosted Machine), or by waiting for the sandbox transport (step 2).
DEVICE_ISOLATED_UNSUPPORTED_MESSAGE = (
    "话题绑定的机器登记为『沙盒』档（visibility=isolated），但按房间隔离的容器传输"
    "尚未实现（#358 第二步）；平台拒绝以裸跑代替——那等于静默把整台机器暴露给这个"
    "房间。请把这台机器改登记为『整台机器（Hosted Machine）』后再继续。"
)


async def resolve_pinned_device(
    service: DeviceService,
    is_online: Callable[[str], bool],
    project_id: uuid.UUID,
    topic_id: uuid.UUID,
) -> str | None:
    """The device this topic's turn must run on (execution-architecture v4 §affinity).

    A topic's work tree + resumable claude session live on ONE machine. So:
      * already pinned → return it **iff online AND still whole-machine**; an offline
        pinned device raises (queue/retry) and an `isolated` pinned device raises the
        #358 「尚未实现」 error — NEVER fall back to another device, which would start
        from an empty tree and corrupt session resume (the original drift bug);
      * not yet pinned (first turn) → pick an online, **non-quarantined**,
        **whole-machine** device serving the project and **pin it** (write-once), so
        every later turn returns to the same machine. Quarantined = judged unhealthy
        by ``device.health`` (#186); a topic that is already pinned is only ever
        moved by the explicit ``agent.host_swap`` flow, never from here.

    The #358 visibility gate lives entirely here (the one resolution point every
    production turn passes through), so an `isolated` device — whose per-room
    container transport is #358 step 2 — is never pinned to a topic nor launched
    bare-on-host in its place: silently degrading `isolated` to bare is exactly the
    whole-machine exposure the gate exists to prevent.

    Returns the device id, or ``None`` when no runnable bound device is online at all
    (the caller turns that into a clean "no online device" turn error)."""
    pinned = await service.topic_device(topic_id)
    if pinned is not None:
        if not is_online(pinned):
            raise ScreenSetupError(DEVICE_OFFLINE_MESSAGE)
        # An ALREADY-pinned device that has since been re-enrolled as `isolated`
        # (its owner flipped it) must refuse rather than run bare — the pin does not
        # move, but the turn will not silently expose the whole machine either.
        pinned_device = await service.get_device(pinned)
        if pinned_device is not None and not has_runnable_transport(
            pinned_device.visibility
        ):
            raise ScreenSetupError(DEVICE_ISOLATED_UNSUPPORTED_MESSAGE)
        return pinned
    # First turn: pick from the machines that are online AND not quarantined. A
    # quarantined machine just failed two turns in a row for a reason that belongs
    # to the box (#186), so pinning a fresh topic to it would hand the next person
    # the failure we already diagnosed. Note this filter applies to the FIRST pin
    # only — moving an ALREADY-pinned topic never happens here, it goes through the
    # explicit, room-visible path in ``agent.host_swap``, because a pin that the
    # resolver can quietly change is the original drift bug.
    healthy = await service.healthy_devices_for_project(project_id, is_online)
    for device in healthy:
        # #358 gate at the PIN: only whole-machine (`host`) devices have a transport
        # today. An `isolated` device's per-room-container transport is #358 step 2;
        # until it lands such a device cannot run a turn, so it must never be pinned
        # — the pin is write-once, and a topic frozen to an unrunnable device would
        # be bricked with no way to move it. Skipping it here (rather than pinning
        # and failing at launch) keeps it out of the affinity freeze entirely.
        if not has_runnable_transport(device.visibility):
            continue
        await service.bind_topic_device(topic_id, device.device_id)
        return device.device_id
    return None


# Addresses that only mean something ON the box. Routing the box's own turns
# through the local LLM gateway / metering proxy is what makes their spend
# visible — but the same value handed to a machine somewhere else names nothing
# there, and the failure is a turn that dies on a connection error with no hint
# why.
_BOX_LOCAL_HOSTS = ("localhost", "127.0.0.1", "172.17.0.1", "172.18.0.1", "litellm")


def uses_tunnel(*, co_located: bool, tunnel_url: str) -> bool:
    """Whether this screen's CONNECT traffic rides the tunnel.

    Only a REMOTE machine ever needs it: a co-located screen shares the box's
    network and reaches the listener's bridge address directly, so tunnelling it
    would add a hop and a failure mode for nothing.

    No tunnel configured means no tunnel — a remote machine then dials
    ``subscription_device_proxy_host`` as it does today. That is right for a flat
    network and wrong for this one, which is exactly why it is a setting rather
    than a guess: the deployment knows whether its machines can reach the box.
    """
    return bool(tunnel_url.strip()) and not co_located


def connect_transport(*, session_token: str, via_tunnel: bool) -> str:
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
        return f"http://127.0.0.1:{settings.subscription_tunnel_local_port}"
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
_DEVICE_PROXY_CA_PATH = "$HOME/.claude/proxy-ca.pem"


def _warn_if_model_endpoint_is_box_local(env: dict[str, str], device_id: str) -> None:
    # ANTHROPIC_BASE_URL is the gateway route; HTTPS_PROXY is the subscription's
    # CONNECT route to the metering proxy. Either one pointing at a box-local
    # address fails identically off-box.
    for key in ("ANTHROPIC_BASE_URL", "HTTPS_PROXY"):
        value = env.get(key, "")
        if any(h in value for h in _BOX_LOCAL_HOSTS):
            logger.error(
                "device %s is remote but its %s is %s, which only "
                "resolves on the backend's own host — its turns will fail to "
                "reach a model. Set a publicly reachable address "
                "(subscription_device_proxy_host for the metering proxy), or "
                "point remote devices back at the upstream.",
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
# read as alive, never as death (see `_confirm_alive`).
_ALIVE_PROBE_TIMEOUT_S = 8.0

# Retire-and-reopen a reused screen whose baked credential is within this many
# seconds of expiry, mirroring the launcher's ``$EXPFILE`` gate (device_launch)
# so the backend's reuse decision and the on-device create gate agree on ONE
# margin. Small on purpose: it only rejects an already-dead-or-dying credential,
# never a healthy one, so a short-lived token (the gateway path's hour) is
# re-minted at most once per margin rather than on every turn.
_CREDENTIAL_EXPIRY_MARGIN_S = 300


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


class DeviceProvider(HooksTurnProvider[HubScreen]):
    """The REMOTE hooks backend: runs interactive `claude` on a user's enrolled
    machine over the frozen link.Msg channel (DeviceHub), streaming AgentEvents
    from Claude Code hooks. Transport = link.Msg + a device screen; the shared
    turn flow lives in the base (HooksTurnProvider) — this class implements only
    the transport seam. The screen ctx is a HubScreen."""

    name = "device"
    _needs_topic_message = "device 后端需要话题上下文（每个屏幕绑定一个话题）"
    _timeout_message = "device 轮次超时"

    def __init__(
        self,
        *,
        hub: DeviceHub | None = None,
        session_factory: async_sessionmaker | None = None,
        device_resolver: DeviceResolver | None = None,
        router: HookRouter | None = None,
        public_base: str | None = None,
        idle_suspect_s: float = 300.0,
        hard_ceiling_s: float = 10800.0,
    ) -> None:
        # Two-layer turn timeout, symmetric with the local tmux backend (turn 活跃度
        # 检测). Below `idle_suspect_s` of no hook AND no liveness evidence a turn is
        # normal; past it it is only SUSPECTED wedged and `_confirm_alive` (the
        # process-tree probe below) is polled until it confirms the screen is
        # actually dead; `hard_ceiling_s` is the unconditional backstop. The old
        # single value collapsed both layers into one 900s deadline that killed a
        # long-but-silent foreground command (a 20-minute pytest emits no interim
        # hook) at minute 15. Defaults mirror the tmux backend
        # (settings.agent_idle_suspect_s / agent_turn_hard_ceiling_s), so the local
        # and remote hooks backends share ONE timeout policy.
        super().__init__(
            router=router,
            idle_suspect_s=idle_suspect_s,
            hard_ceiling_s=hard_ceiling_s,
        )
        self._hub = hub or device_hub
        self._session_factory = session_factory
        # A resolver may be injected (tests / future routing); otherwise the DB-backed
        # resolver is used lazily (keeps this module importable without a DB).
        self._device_resolver = device_resolver
        self._public_base = (public_base or settings.connector_public_base).rstrip("/")
        # (project, topic) → was that turn's device co-located? Written when a
        # turn resolves its device, read by checkpoint() afterwards.
        self._co_located_at: dict[tuple[uuid.UUID, uuid.UUID], bool] = {}

    def available(self) -> bool:
        """Whether any device is currently connected (online). Project-level checks
        happen per turn in ``run_turn``."""
        return bool(self._hub.online_device_ids())

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

    async def _is_co_located(self, device_id: str) -> bool:
        """Whether this device shares the backend's filesystem.

        ``device_shared_workspace_host_root`` is deployment-wide, but a deployment
        can host BOTH kinds of device at once: the box cheese itself runs on, and
        machines it provisioned from MicroCloud. A platform-provisioned machine is
        on its own host and shares nothing — and getting this wrong fails SILENTLY:
        the launcher `mkdir -p`s whatever path it is given, so the agent would open
        a turn in an empty directory instead of the topic's worktree.

        Reads ``device.supply`` (#282 决定 2). This used to ask the machine table
        「有没有一行指向这个 device」 — the reverse lookup #282 is about. Same
        answer, but now the fact is stored where it is used, so a `cloud` device
        that never got a ``project_machines`` row (a future provisioning path)
        cannot silently read as co-located and open a turn in an empty directory.

        An unknown device keeps the previous reading (co-located when the shared
        root is set): the same behaviour this had for any device with no machine
        row, and the deployments that set that root are single-box ones.
        """
        if not settings.device_shared_workspace_host_root.strip():
            return False
        factory = self._session_factory
        if factory is None:
            from app.core.db import async_session_factory

            factory = async_session_factory
        async with factory() as session:
            device = await sql_device_service(session).get_device(device_id)
        return device is None or device.supply is not Supply.cloud

    async def _device_ccproxy_upstream(self, device_id: str) -> str:
        """The ccproxy identity this DEVICE brings, '' when it brings none.

        Non-empty means the device runs on the machine-ticket model — claude
        carries the device's own ccproxy ticket and the meter relays it over
        this identity — the same credential shape as an enrolled MicroCloud
        machine (whose identity lives on `ProjectMachine` and whose launches are
        already steered by CHEESE_TUNNEL_URL). Kept separate from
        `_is_co_located` on purpose: the two facts change independently, and
        that method is monkeypatched all over the tests."""
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

    def _work_dir(
        self, project_id: uuid.UUID, topic_id: uuid.UUID, *, co_located: bool
    ) -> str:
        """The screen's cwd. For a CO-LOCATED device (one sharing this backend's
        filesystem, ``device_shared_workspace_host_root`` set) this is the topic's
        REAL worktree, translated from the container path to the host root the device
        sees — so device edits land in the topic branch and checkpoint/accept work
        with no clone/sync. Otherwise a per-topic scratch dir the launcher creates."""
        host_root = settings.device_shared_workspace_host_root.strip()
        if co_located and host_root:
            wt = ws.topic_worktree(
                project_id, topic_id
            ).resolve()  # materializes + chmods
            container_root = Path(settings.workspace_root).resolve()
            try:
                return str(Path(host_root) / wt.relative_to(container_root))
            except ValueError:
                # worktree outside workspace_root (shouldn't happen) — fall through
                # to a scratch dir rather than hand the device an unrelated host path.
                pass
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
        result = await self._hub.exec(
            device_id,
            ["sh", "-c", f'mkdir -p "$HOME/.cheese/launch" && cat > "{path}"'],
            stdin=script,
            timeout=30,
        )
        if result.get("exit") != 0:
            raise ScreenSetupError(
                f"无法把启动脚本写到设备上：{result.get('stderr') or result}"
            )
        return ["bash", "-lc", f'exec bash "{path}"']

    async def _ensure_screen(
        self,
        *,
        device_id: str,
        agent_user_id: int,
        agent_handle: str,
        project_id: uuid.UUID,
        topic_id: uuid.UUID,
        token: str,
        model: str | None,
        env: dict[str, str] | None,
        system_prompt: str = "",
    ) -> HubScreen:
        """Reuse the topic's screen on the device, or open a fresh one running
        ``claude`` with our hooks (the device-side launcher creates its home/work dirs
        and wires the hook forwarder). A reused screen is RE-ASSERTED, not trusted:
        the hub's registry survives things the device's sessions do not (a connector
        restart kills its private tmux; a create sent on a dying transport was never
        delivered at all), and the frozen cli silently drops ``rpc.call`` for a sid
        it does not know — so a turn that trusted the registry alone died in a blank
        3×60s prompt timeout whenever the two had diverged. The adopt-create is
        idempotent on the device: a live session hot-reloads the cheeselet and keeps
        the system prompt it launched with (the launcher only reads it at screen
        creation); a lost one is respawned under the same sid + screen token.

        But adopt-create only respawns a screen the CONNECTOR forgot (it restarted);
        it cannot respawn one whose `claude` died while the connector kept running,
        because the connector still holds the sid and merely hot-reloads into the
        dead pane. So a reused screen is first probed for a live `claude`
        (``_confirm_alive``); an explicitly dead one is closed and reopened under a
        fresh sid the connector must Spawn, rather than reasserted into a corpse."""
        existing = self._existing_screen(device_id, topic_id)
        if existing is not None and self._credential_is_stale(existing):
            # #388 缺陷二: the screen is still alive, but the credential its `claude`
            # was LAUNCHED with has expired (or is within the retire margin). That
            # credential is read ONCE at startup and never re-read, and a reused
            # screen is only reasserted (a cheeselet hot-reload), never relaunched —
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
        if existing is not None and not await self._confirm_alive(existing):
            # The hub still has a screen for this topic, but the `claude` behind it
            # is GONE — its tmux session was killed out from under a STILL-RUNNING
            # connector (an orphan sweep, a `tmux kill-server`, a crash). Reasserting
            # (adopt-create, #369) does NOT bring it back: the frozen connector,
            # finding the sid still in its own in-memory session map, only
            # hot-reloads the cheeselet and returns — it re-Spawns the launcher ONLY
            # for a sid it has forgotten, i.e. after IT restarted (cli host.go
            # createSession). #369 rebuilds a screen a CONNECTOR restart lost; it
            # cannot rebuild one whose `claude` died while the connector lived. The
            # turn would then prompt a dead pane and die in the 25s
            # "会话没有任何反应" delivery timeout, reaching no model — which on a
            # subscription deployment is a co-located device that silently never
            # bills a turn (#325 G2). Drop the stale screen (session.close makes the
            # connector forget the sid too) so the code below OPENS a fresh one under
            # a NEW sid the connector cannot short-circuit and must Spawn: the
            # launcher runs, `claude` restarts, hooks flow. Only an explicit `dead`
            # reading forces this (see `_confirm_alive`) — an alive, `unknown`, or
            # probe-hiccup screen is still reasserted, exactly as before.
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
        home_dir = f"$HOME/.cheese/home/{project_id}/{topic_id}"
        co_located = await self._is_co_located(device_id)
        # checkpoint() runs after the turn, from a caller that has no device in
        # hand — remember what this device is, or the snapshot decision falls back
        # to the deployment-wide switch and is wrong for every remote machine.
        self._co_located_at[(project_id, topic_id)] = co_located
        work_dir = self._work_dir(project_id, topic_id, co_located=co_located)
        ca_pem = ""
        if settings.subscription_enabled:
            # Subscription deployment: EVERY device turn — co-located and remote
            # alike — runs on the subscription through the metering proxy, the
            # same path the local tmux container takes (#325 G2). The machine
            # holds no real credential either way: the env ships a scoped cheese
            # token as the fake login, the proxy verifies it and injects the real
            # token backend-side. The gateway route (/llm → LiteLLM) is NOT a
            # fallback here — falling back silently is exactly the model swap
            # this branch exists to kill (dev shipped device screens with
            # CLAUDE_MODEL=deepseek-chat while users thought they were talking
            # to Claude).
            ca_pem = _read_proxy_ca()
            # Session-length TTL, not the 1h default. This token is baked into the
            # bare process's HTTPS_PROXY (CONNECT credential) and
            # CLAUDE_CODE_OAUTH_TOKEN, both read ONCE at process start and never
            # hot-refreshed; the screen is reused across turns (a reassert only
            # hot-reloads the cheeselet, it does not relaunch claude). A 1h token
            # thus expires under a still-running process, and every turn after the
            # first hour is rejected by the metering proxy (407) — the agent looks
            # dead. Same session lifetime as the CHEESE_TOKEN minted alongside it.
            session_token = mint_scoped_token(
                project_id=str(project_id),
                topic_id=str(topic_id),
                ttl_s=SESSION_TOKEN_TTL_S,
            )
            tunnel_url = settings.subscription_tunnel_url.strip()
            via_tunnel = uses_tunnel(co_located=co_located, tunnel_url=tunnel_url)
            connect_proxy_url = connect_transport(
                session_token=session_token, via_tunnel=via_tunnel
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
            # subscription_provider only ADDS keys (mirrors tmux_provider).
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
            if via_tunnel:
                # Read by the launch script: it writes the helper and the token
                # file, and starts the helper before `claude`. Carried on the env
                # rather than as arguments because a remote machine's launch is
                # built entirely from `extra_env` — there is no other channel
                # into that builder.
                merged["CHEESE_TUNNEL_URL"] = tunnel_url
                merged["CHEESE_TUNNEL_PORT"] = str(
                    settings.subscription_tunnel_local_port
                )
            elif await self._device_ccproxy_upstream(device_id):
                # A CO-LOCATED device that brings its own ccproxy identity (the
                # dev box, registered on `DeviceRow.ccproxy_upstream`) runs on
                # the machine-ticket model exactly like a tunneled MicroCloud
                # machine: the launcher's reconcile hands claude the device's
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
                model=settings.agent_model,
            )
            model_env = {**provider.env, **(env or {})}
            # Same stamp on the gateway path: the model credential is the scoped
            # `token`, and its expiry is what both the launcher and the reuse gate
            # check before adopting.
            credential_expires = _credential_expiry(token)
            model_env["CHEESE_TOKEN_EXPIRES"] = str(credential_expires)
        if not co_located:
            _warn_if_model_endpoint_is_box_local(model_env, device_id)
        command, screen_env, cheeselet = build_screen_launch(
            hook_url=self._hook_url(topic_id),
            hook_token=token,
            home_dir=home_dir,
            work_dir=work_dir,
            model=model,
            extra_env=model_env,
            api_base=f"{self._public_base}/api",
            cli_url=f"{self._public_base}/sandbox/cli/cheese",
            project_id=str(project_id),
            topic_id=str(topic_id),
            author=agent_handle,
            # A machine on its own host has no worktree to edit, so it clones the
            # project and pushes the topic branch back. Same origin + same scoped
            # token the platform CLI already uses from this machine — one
            # convention, so there is a single place to be wrong about the prefix.
            git_remote=(
                None
                if co_located
                else f"{self._public_base}/api/projects/{project_id}/git"
            ),
            git_branch=ws.branch_for_topic(topic_id),
            system_prompt=system_prompt,
            ca_pem=ca_pem,
        )
        command = await self._ship_launcher(device_id, topic_id, command)
        if existing is not None:
            # A reassert keeps the CURRENTLY-RUNNING `claude`, which still holds the
            # credential it was born with — so the recorded birth expiry must NOT be
            # overwritten with this launch's freshly-minted one (the new token never
            # reaches the running process). It stays as the reuse gate's truth.
            await self._hub.reassert_screen(
                existing, command=command, cheeselet_source=cheeselet, env=screen_env
            )
            return existing
        screen = await self._hub.open_screen(
            device_id,
            command,
            cheeselet,
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
        return screen

    # --- turn --------------------------------------------------------------

    async def _precheck(
        self, project_id: uuid.UUID, topic_id: uuid.UUID
    ) -> tuple[str, int, str]:
        """Resolve the topic's pinned/online device + its agent identity BEFORE the
        base claims the topic's hook queue (pre-refactor ordering, review finding).
        The resolved tuple is handed back to ``_ensure_ready`` via ``precheck``.
        Raises ``ScreenSetupError`` (offline pinned device, or none online)."""
        resolved = await self._resolve_device_agent(project_id, topic_id)
        if resolved is None:
            raise ScreenSetupError(
                "没有在线的绑定设备可运行本轮（self-hosted 设备未连接）"
            )
        return resolved

    async def _ensure_ready(
        self,
        *,
        project_id: uuid.UUID,
        topic_id: uuid.UUID,
        token: str,
        model: str | None,
        env: dict[str, str] | None,
        memory_scope: str | None,
        owner: str | None,
        turn_id: uuid.UUID | None,
        resume_session_id: str | None,
        system_prompt: str,
        precheck: object,
    ) -> HubScreen:
        """Reuse/open the topic's screen running `claude` with our hooks on the
        device resolved by ``_precheck``; return the screen (ctx). Raises
        ScreenSetupError when the screen fails."""
        assert isinstance(precheck, tuple)  # from our _precheck
        device_id, agent_user_id, agent_handle = precheck
        try:
            return await self._ensure_screen(
                device_id=device_id,
                agent_user_id=agent_user_id,
                agent_handle=agent_handle,
                project_id=project_id,
                topic_id=topic_id,
                token=token,
                model=model,
                env=env,
                system_prompt=system_prompt,
            )
        except Exception as exc:  # noqa: BLE001 — any setup failure ends the turn
            # str(exc) is EMPTY for a bare TimeoutError — the failure that used to
            # reach the room as 「device 后端启动失败：」 with nothing after the
            # colon. Fall back to the exception type so the message always says
            # *something* about what went wrong.
            raise ScreenSetupError(
                f"device 后端启动失败：{str(exc) or exc.__class__.__name__}"
            ) from exc

    async def _send_prompt(self, screen: HubScreen, prompt: str) -> None:
        """Deliver the prompt via the minimal cheeselet's `prompt` (it gates on the
        `❯` input box first, so a fresh screen's first prompt is not dropped)."""
        try:
            call_id = await self._hub.call_screen(
                screen.device_id, screen.sid, "prompt", [prompt]
            )
            await self._hub.await_call(screen.device_id, call_id, timeout=60)
        except Exception as exc:  # noqa: BLE001 — a failed prompt ends the turn
            raise ScreenSetupError(
                f"device 后端启动失败：{str(exc) or exc.__class__.__name__}"
            ) from exc

    def _credential_is_stale(self, screen: HubScreen) -> bool:
        """Whether the credential this screen's `claude` was LAUNCHED with has
        expired, or is within the retire margin of it (#388 缺陷二).

        The freshness of that credential is part of whether a screen may be REUSED,
        not just whether its process is alive: `claude` reads its model credential
        (HTTPS_PROXY CONNECT password / CLAUDE_CODE_OAUTH_TOKEN) exactly once at
        startup, and a reused screen is only reasserted (a cheeselet hot-reload),
        never relaunched — so a still-running process on a dead credential is
        rejected on every request while the process-tree probe (`_confirm_alive`)
        keeps reporting it healthy. This is the local, in-memory half of the gate;
        it never touches the device.

        Conservative in the same direction as `_confirm_alive`: a screen with no
        recorded expiry (`None` — adopted after a server restart, or a dev token
        with no decodable claim) is treated as FRESH and never retired on missing
        information, so we only ever retire a credential we can prove is dying."""
        exp = screen.credential_expires
        if exp is None:
            return False
        return exp <= int(time.time()) + _CREDENTIAL_EXPIRY_MARGIN_S

    async def _confirm_alive(self, screen: HubScreen) -> bool:
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
        to a live browser viewer). The same call serves a co-located device and a
        remote one — it is NOT gated on co-location.

        Conservative, mirroring TmuxHooksProvider._confirm_alive: only an explicit
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

    def checkpoint(self, project_id: uuid.UUID, topic_id: uuid.UUID) -> None:
        """A CO-LOCATED device edited the backend's REAL worktree this turn, so
        snapshot it into version history exactly like the local path (else 采纳/diff
        wouldn't see the edits). A REMOTE device owns its own tree → still a no-op
        (it pushes its own work back over git instead). Held while a `cheese await`
        command is still writing that tree, same as the local path."""
        if self._co_located_at.get((project_id, topic_id)):
            awaited_tasks.checkpoint_worktree(project_id, topic_id)

    # --- teardown ----------------------------------------------------------

    async def release_topic(self, project_id: uuid.UUID, topic_id: uuid.UUID) -> None:
        """Free a done topic's screen — the device backend's teardown, symmetric to
        the local backend's ``ws.stop_topic_container`` and to this provider's own
        ``checkpoint``. Called when a topic is accepted/archived or reaped for being
        idle; without it the screen (and the ``claude`` process + tmux session behind
        it) leaks on the machine forever, since the reaper only ever knew how to free
        Docker containers.

        A CO-LOCATED device edited the backend's REAL worktree, so ONLY its screen is
        closed — the tree belongs to the backend and must never be deleted here. A
        REMOTE device owns its clone under a per-topic scratch dir, so that dir is
        removed too (the per-project home dir is shared across a project's topics →
        never touched).

        Best-effort and idempotent: no screen (device offline / already gone) is a
        successful no-op, and every failure is swallowed so one topic can never break
        a reap loop. The screen is forgotten even when its device is offline, so an
        archived topic leaves no stale registry entry behind."""
        for screen in self._hub.screens_for_topic(topic_id):
            device_id = screen.device_id
            try:
                # Close first so the device's claude process stops holding the tree,
                # THEN remove the (now idle) remote clone. close_screen forgets the
                # screen even for an offline device (its session_close is a no-op),
                # so our registry never leaks an archived topic.
                await self._hub.close_screen(device_id, screen.sid)
                await self._remove_remote_work_dir(device_id, project_id, topic_id)
            except Exception:  # noqa: BLE001 — one screen must not stop the rest
                logger.warning(
                    "release_topic: failed freeing screen %s on device %s (topic %s)",
                    screen.sid,
                    device_id,
                    topic_id,
                    exc_info=True,
                )

    async def _remove_remote_work_dir(
        self, device_id: str, project_id: uuid.UUID, topic_id: uuid.UUID
    ) -> None:
        """Remove a REMOTE device's per-topic work dir on the box. A co-located device
        is skipped entirely — its work dir IS the backend's real worktree, deleting it
        would destroy the topic's branch. Only runs while the device is online (an
        offline box is unreachable — nothing to remove now). The delete target is
        always the scratch path ``_work_dir(co_located=False)`` returns, never the
        co-located worktree translation, so even a mis-classification cannot reach the
        backend's tree."""
        if not self._hub.is_online(device_id):
            return
        if await self._is_co_located(device_id):
            return
        work_dir = self._work_dir(project_id, topic_id, co_located=False)
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
    """Free a topic's device screen from a caller that holds no ``DeviceProvider`` —
    the accept/archive path and the idle reaper both reach compute through ws-level
    helpers, not the compute pool. Thin wrapper over ``DeviceProvider.release_topic``
    bound to the shared ``device_hub`` singleton (``hub=None``). Best-effort and
    idempotent, so it is safe to call for EVERY archived/idle topic regardless of
    backend: a topic that never ran on a device simply has no screen to free."""
    provider = DeviceProvider(hub=hub, session_factory=session_factory)
    await provider.release_topic(project_id, topic_id)


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
