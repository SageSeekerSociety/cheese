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
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import settings
from app.domain.agent import awaited_tasks, provider_env
from app.domain.agent.device_hub import DeviceHub, HubScreen, device_hub
from app.domain.agent.device_launch import build_screen_launch
from app.domain.agent.hook_events import HookRouter
from app.domain.agent.hooks_substrate import HooksTurnProvider, ScreenSetupError
from app.domain.device.service import DeviceService
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


async def resolve_pinned_device(
    service: DeviceService,
    is_online: Callable[[str], bool],
    project_id: uuid.UUID,
    topic_id: uuid.UUID,
) -> str | None:
    """The device this topic's turn must run on (execution-architecture v4 §affinity).

    A topic's work tree + resumable claude session live on ONE machine. So:
      * already pinned → return it **iff online**; if the pinned device is offline,
        raise (queue/retry) — NEVER fall back to another device, which would start
        from an empty tree and corrupt session resume (the original drift bug);
      * not yet pinned (first turn) → pick an online device serving the project and
        **pin it** (write-once), so every later turn returns to the same machine.

    Returns the device id, or ``None`` when no bound device is online at all (the
    caller turns that into a clean "no online device" turn error)."""
    pinned = await service.topic_device(topic_id)
    if pinned is not None:
        if is_online(pinned):
            return pinned
        raise ScreenSetupError(
            "话题绑定的算力设备已离线，请重新连接该设备再继续本轮"
            "（不会漂到别的设备，以免工作树/会话错乱）"
        )
    for device in await service.list_devices_for_project(project_id):
        if is_online(device.device_id):
            await service.bind_topic_device(topic_id, device.device_id)
            return device.device_id
    return None


# Addresses that only mean something ON the box. Routing the box's own turns
# through the local LLM gateway is what makes their spend visible — but the same
# value handed to a machine somewhere else names nothing there, and the failure
# is a turn that dies on a connection error with no hint why.
_BOX_LOCAL_HOSTS = ("localhost", "127.0.0.1", "172.17.0.1", "172.18.0.1", "litellm")


def _warn_if_model_endpoint_is_box_local(env: dict[str, str], device_id: str) -> None:
    base = env.get("ANTHROPIC_BASE_URL", "")
    if any(h in base for h in _BOX_LOCAL_HOSTS):
        logger.error(
            "device %s is remote but its ANTHROPIC_BASE_URL is %s, which only "
            "resolves on the backend's own host — its turns will fail to reach a "
            "model. Set a publicly reachable gateway URL, or point remote devices "
            "back at the upstream.",
            device_id,
            base,
        )


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
        turn_timeout_s: float = 900.0,
    ) -> None:
        # TODO(turn 活跃度检测): the local tmux backend got a two-layer idle-suspect
        # + hard-ceiling timeout (capture-pane polling + pane_dead probe); the
        # remote device backend explicitly did NOT — the design doc flagged "what's
        # an equivalent lightweight activity/liveness probe for a device screen"
        # as still-open (device_hub's per-screen bytes aren't wired for this yet).
        # Passing the same value for both layers reduces run_hooks_turn's two-layer
        # check back to the old single static deadline, so behaviour here is
        # UNCHANGED until that follow-up lands.
        super().__init__(
            router=router, idle_suspect_s=turn_timeout_s, hard_ceiling_s=turn_timeout_s
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
        machines it provisioned from MicroCloud. A provisioned machine is on its
        own host and shares nothing — and getting this wrong fails SILENTLY: the
        launcher `mkdir -p`s whatever path it is given, so the agent would open a
        turn in an empty directory instead of the topic's worktree.
        """
        if not settings.device_shared_workspace_host_root.strip():
            return False
        from app.domain.machine.repositories import ProjectMachineRepository

        factory = self._session_factory
        if factory is None:
            from app.core.db import async_session_factory

            factory = async_session_factory
        async with factory() as session:
            provisioned = await ProjectMachineRepository(session).is_provisioned_device(
                device_id
            )
        return not provisioned

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
    ) -> HubScreen:
        """Reuse the topic's screen on the device, or open a fresh one running
        ``claude`` with our hooks (the device-side launcher creates its home/work dirs
        and wires the hook forwarder)."""
        existing = self._existing_screen(device_id, topic_id)
        if existing is not None:
            return existing
        # Device-side paths (the launcher mkdir -p's them). Kept under a stable per
        # project/topic root so the screen's git-backed work persists across turns.
        home_dir = f"$HOME/.cheese/home/{project_id}"
        co_located = await self._is_co_located(device_id)
        # checkpoint() runs after the turn, from a caller that has no device in
        # hand — remember what this device is, or the snapshot decision falls back
        # to the deployment-wide switch and is wrong for every remote machine.
        self._co_located_at[(project_id, topic_id)] = co_located
        work_dir = self._work_dir(project_id, topic_id, co_located=co_located)
        # A remote machine gets the backend's own model route and its scoped
        # token — never the upstream provider key. The backend substitutes the
        # project's virtual key, so the credential stays on the box and spend is
        # attributed without having to trust the machine to report it.
        provider = provider_env.api_key_provider(
            gateway_base=f"{self._public_base}/llm",
            key=token,
            model=settings.agent_model,
        )
        gateway_env = {**provider.env, **(env or {})}
        if not co_located:
            _warn_if_model_endpoint_is_box_local(gateway_env, device_id)
        command, screen_env, cheeselet = build_screen_launch(
            hook_url=self._hook_url(topic_id),
            hook_token=token,
            home_dir=home_dir,
            work_dir=work_dir,
            model=model,
            extra_env=gateway_env,
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
        )
        return await self._hub.open_screen(
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
            )
        except Exception as exc:  # noqa: BLE001 — any setup failure ends the turn
            raise ScreenSetupError(f"device 后端启动失败：{exc}") from exc

    async def _send_prompt(self, screen: HubScreen, prompt: str) -> None:
        """Deliver the prompt via the minimal cheeselet's `prompt` (it gates on the
        `❯` input box first, so a fresh screen's first prompt is not dropped)."""
        try:
            call_id = await self._hub.call_screen(
                screen.device_id, screen.sid, "prompt", [prompt]
            )
            await self._hub.await_call(screen.device_id, call_id, timeout=60)
        except Exception as exc:  # noqa: BLE001 — a failed prompt ends the turn
            raise ScreenSetupError(f"device 后端启动失败：{exc}") from exc

    def checkpoint(self, project_id: uuid.UUID, topic_id: uuid.UUID) -> None:
        """A CO-LOCATED device edited the backend's REAL worktree this turn, so
        snapshot it into version history exactly like the local path (else 采纳/diff
        wouldn't see the edits). A REMOTE device owns its own tree → still a no-op
        (it pushes its own work back over git instead). Held while a `cheese await`
        command is still writing that tree, same as the local path."""
        if self._co_located_at.get((project_id, topic_id)):
            awaited_tasks.checkpoint_worktree(project_id, topic_id)
