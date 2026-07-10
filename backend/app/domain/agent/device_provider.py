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

``checkpoint`` is a no-op here: the device owns its own working tree (a future
extension can ``exec`` a git snapshot on the device over the link).
"""

import uuid
from collections.abc import AsyncIterator, Awaitable, Callable

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import settings
from app.core.sandbox_auth import mint_scoped_token
from app.domain.agent.device_hub import DeviceHub, HubScreen, device_hub
from app.domain.agent.device_launch import build_screen_launch
from app.domain.agent.hook_events import HookRouter, hook_router
from app.domain.agent.hooks_substrate import SESSION_TOKEN_TTL_S, run_hooks_turn
from app.domain.agent.service import AgentEvent, AgentResult
from app.domain.device.service import DeviceService
from app.domain.device.sql_repository import SqlDeviceRepository
from app.domain.user.models import User

# Resolve an online device serving a project → (device_id, agent_user_id, agent_handle).
DeviceResolver = Callable[[uuid.UUID], Awaitable["tuple[str, uuid.UUID, str] | None"]]

# The device session's hook token outlives one turn (the screen is reused), so it is
# scoped to the topic with a session-length TTL — a stale one still can't reach another
# topic. Shared with the tmux backend (hooks_substrate.SESSION_TOKEN_TTL_S).
_SESSION_TOKEN_TTL_S = SESSION_TOKEN_TTL_S


class DeviceProvider:
    """ComputeProvider that runs a turn on an enrolled device's screen and streams
    AgentEvents from Claude Code hooks."""

    name = "device"

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
        self._hub = hub or device_hub
        self._session_factory = session_factory
        # A resolver may be injected (tests / future routing); otherwise the DB-backed
        # resolver is used lazily (keeps this module importable without a DB).
        self._device_resolver = device_resolver
        self._router = router or hook_router
        self._public_base = (public_base or settings.connector_public_base).rstrip("/")
        self._turn_timeout_s = turn_timeout_s

    def available(self) -> bool:
        """Whether any device is currently connected (online). Project-level checks
        happen per turn in ``run_turn``."""
        return bool(self._hub.online_device_ids())

    # --- device / screen resolution ----------------------------------------

    async def _resolve_device_agent(
        self, project_id: uuid.UUID
    ) -> tuple[str, uuid.UUID, str] | None:
        """An online device serving ``project_id`` → ``(device_id, agent_user_id,
        agent_handle)``, or ``None`` when no bound device is online."""
        if self._device_resolver is not None:
            return await self._device_resolver(project_id)
        factory = self._session_factory
        if factory is None:
            from app.core.db import async_session_factory

            factory = async_session_factory
        async with factory() as session:
            service = DeviceService(SqlDeviceRepository(session))
            for device in await service.list_devices_for_project(project_id):
                if self._hub.is_online(device.device_id):
                    agent = await session.get(User, device.agent_user_id)
                    handle = agent.handle if agent is not None else "agent"
                    return device.device_id, device.agent_user_id, handle
        return None

    def _existing_screen(
        self, device_id: str, topic_id: uuid.UUID
    ) -> HubScreen | None:
        for screen in self._hub.all_online_screens():
            if screen.device_id == device_id and screen.topic_id == topic_id:
                return screen
        return None

    def _hook_url(self, topic_id: uuid.UUID) -> str:
        # Reuse the existing sandbox hook endpoint (scoped-token auth + shared
        # hook_router), so the device path adds no second hook surface.
        return f"{self._public_base}/sandbox/hooks/{topic_id}"

    async def _ensure_screen(
        self,
        *,
        device_id: str,
        agent_user_id: uuid.UUID,
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
        work_dir = f"$HOME/.cheese/work/{project_id}/{topic_id}"
        gateway_env = {**settings.agent_env(), **(env or {})}
        command, screen_env, cheeselet = build_screen_launch(
            hook_url=self._hook_url(topic_id),
            hook_token=token,
            home_dir=home_dir,
            work_dir=work_dir,
            model=model,
            extra_env=gateway_env,
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

    async def run_turn(
        self,
        *,
        project_id: uuid.UUID,
        topic_id: uuid.UUID | None,
        prompt: str,
        system_prompt: str,
        resume_session_id: str | None,
        model: str | None = None,
        env: dict[str, str] | None = None,
        memory_scope: str | None = None,
        owner: str | None = None,
        turn_id: uuid.UUID | None = None,
        sandbox_image: str | None = None,
        images: list[dict] | None = None,
    ) -> AsyncIterator[AgentEvent]:
        if topic_id is None:
            yield AgentResult(
                text="device 后端需要话题上下文（每个屏幕绑定一个话题）",
                session_id=resume_session_id,
                is_error=True,
            )
            return

        resolved = await self._resolve_device_agent(project_id)
        if resolved is None:
            yield AgentResult(
                text="没有在线的绑定设备可运行本轮（self-hosted 设备未连接）",
                session_id=resume_session_id,
                is_error=True,
            )
            return
        device_id, agent_user_id, agent_handle = resolved

        topic_key = str(topic_id)
        token = mint_scoped_token(
            project_id=str(project_id), topic_id=topic_key, ttl_s=_SESSION_TOKEN_TTL_S
        )

        # Register the queue BEFORE the prompt so no hook is missed (same discipline
        # as the tmux backend).
        queue = self._router.register(topic_key)
        try:
            try:
                screen = await self._ensure_screen(
                    device_id=device_id,
                    agent_user_id=agent_user_id,
                    agent_handle=agent_handle,
                    project_id=project_id,
                    topic_id=topic_id,
                    token=token,
                    model=model,
                    env=env,
                )
                # The cheeselet gates on the `❯` input box before typing, so a fresh
                # screen's first prompt is not dropped. Await its ack (or error).
                call_id = await self._hub.call_screen(
                    device_id, screen.sid, "prompt", [prompt]
                )
                await self._hub.await_call(device_id, call_id, timeout=60)
            except Exception as exc:  # noqa: BLE001 — any setup/prompt failure ends the turn
                yield AgentResult(
                    text=f"device 后端启动失败：{exc}",
                    session_id=resume_session_id,
                    is_error=True,
                )
                return

            # Shared drain loop (hooks_substrate): transport-specific work above
            # (open a device screen + prompt over link.Msg) is done; sensing is
            # identical to the tmux backend from here.
            async for event in run_hooks_turn(
                queue=queue,
                turn_timeout_s=self._turn_timeout_s,
                resume_session_id=resume_session_id,
                timeout_message="device 轮次超时",
            ):
                yield event
        finally:
            self._router.unregister(topic_key, queue)

    def checkpoint(self, project_id: uuid.UUID, topic_id: uuid.UUID) -> None:
        """No-op: the device owns its working tree. A future extension can exec a git
        snapshot on the device over the link (best-effort, never fail a turn)."""
        return
