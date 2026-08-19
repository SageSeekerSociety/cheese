"""ComputePool: the compute side of the two-pool model (design v2 R2 / v3).

Symmetric to AIPool (profiles.py). A ComputeProvider OWNS execution of an agent
turn: it builds the sandbox (container + worktree + session + cheese env), runs
the turn, and checkpoints the worktree afterward. The turn path talks to the
pool, never to a sandbox dict or a cli_path — so a remote / GPU / competition
node slots in behind the same `run_turn`/`checkpoint` contract without touching
callers (review Finding R2: the provider seam is a turn executor that owns
compute, not an exec(argv)/cli_path detail leaked to the orchestrator).

Today `LocalDockerProvider` runs the Claude SDK in-process against the local
per-topic Docker sandbox. Tomorrow `RemoteCheesedProvider` reimplements the same
two methods by relocating execution to a cheesed node and relaying the event
stream + git refs back.
"""

import json
import subprocess
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

import httpx

from app.core.background import spawn
from app.core.config import settings
from app.core.sandbox_auth import mint_scoped_token
from app.domain.agent import awaited_tasks
from app.domain.agent.sandbox_notices import warn_container_rebuilt
from app.domain.agent.service import (
    AgentEvent,
    AgentService,
    event_from_dict,
)
from app.domain.identity.handles import topic_agent_handle
from app.domain.workspace import service as ws

if TYPE_CHECKING:
    from app.domain.agent.harness.claude_code import (
        HookActivityConsumer,
        HookEventConsumer,
        TopicSubscription,
    )

# Author handle for 芝士's cheese-CLI callbacks (kept here to avoid importing
# chat.py, which imports this module).

# Native tools 芝士 may use inside the sandbox + the Task tools (live todo).
_SANDBOX_TOOLS = [
    "Bash",
    "Read",
    "Write",
    "Edit",
    "Grep",
    "Glob",
    "TaskCreate",
    "TaskUpdate",
    "TaskList",
    "TaskGet",
]


class ComputeProvider(Protocol):
    """Runs one agent turn (owning the sandbox) and checkpoints its workspace.
    The unit a remote node reimplements by relocating execution + relaying the
    event stream / git refs over RPC."""

    name: str

    # 图片输入: whether THIS provider actually embeds `images=` into the turn's
    # user message. It is a capability, not a preference — the prompt wording
    # branches on it (chat._prompt_line). Before this existed, `images=` was
    # accepted by every provider and silently dropped by the hooks-driven ones,
    # while the prompt kept telling 芝士 "图片内容已附在本条消息里" on all of
    # them. An agent that reads that promise and sees nothing does not error —
    # it invents what the image said, which is worse than saying "我没收到图".
    # Default True keeps the SDK/relay contract; a backend that drops images
    # MUST override it to False rather than leave the prompt lying for it.
    embeds_images: bool

    def available(self) -> bool: ...

    def run_turn(
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
    ) -> AsyncIterator[AgentEvent]: ...

    async def deliver(
        self, topic_id: uuid.UUID, text: str, images: list[dict] | None = None
    ) -> bool:
        """Inject text into the turn already running on this topic, if this
        transport can. False = "I have no live screen for it" — the caller then
        runs an ordinary turn. Only the hooks-driven backends (a long-lived
        interactive Claude Code) can say True; a per-turn subprocess has nothing
        to inject into once its turn is over."""
        ...

    def checkpoint(self, project_id: uuid.UUID, topic_id: uuid.UUID) -> None: ...


class LocalDockerProvider:
    """Default provider: builds the local per-topic Docker sandbox and runs the
    SDK in-process. When Docker is absent (tests / degraded) it runs a plain
    model turn with no platform tools — same behaviour as before, now owned here
    instead of in ChatService."""

    name = "local-docker"
    # SDK path: build_query_input base64-embeds every image into the user message.
    embeds_images = True

    def __init__(
        self, *, agent: AgentService, workspace_root: str, sandbox_enabled: bool
    ):
        self._agent = agent
        self._workspace_root = workspace_root
        self._sandbox_enabled = sandbox_enabled

    def available(self) -> bool:
        return True

    def sandboxed(self) -> bool:
        """Whether this turn runs in a real sandbox (Docker present + enabled)."""
        return self._sandbox_enabled and ws.sandbox_available()

    def _workspace_for(self, project_id: uuid.UUID) -> str:
        path = Path(self._workspace_root) / str(project_id)
        path.mkdir(parents=True, exist_ok=True)
        return str(path)

    def _warn_if_image_switch(
        self, topic_id: uuid.UUID, container: str, resolved_image: str
    ) -> None:
        """Read-only mirror of the sandbox shim's own check (claude-sbx): if the
        topic's container is already running a different image than what this
        turn resolved to, the shim is about to `docker rm -f` it (no grace
        period) and rebuild — taking any interactive session / background
        process in the old box with it. Warn the topic before that happens.
        Fire-and-forget (schedules the notice, doesn't await it) so a slow DB
        write never delays turn start; skipped outside a running loop (e.g.
        sync tests) and on a fresh/absent container (nothing to lose)."""
        if not self.sandboxed():
            return
        result = subprocess.run(
            ["docker", "inspect", "-f", "{{.Config.Image}}", container],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0 or result.stdout.strip() == resolved_image:
            return
        # Strong reference + no-op without a loop; see app/core/background.
        # This one tells the topic its box (and everything running in it) was
        # rebuilt — a notice that silently doesn't arrive is worse than none.
        spawn(
            warn_container_rebuilt(topic_id, "image"),
            name=f"image switch notice topic={topic_id}",
        )

    def _sandbox_config(
        self,
        project_id: uuid.UUID,
        topic_id: uuid.UUID,
        *,
        memory_scope: str | None,
        owner: str | None,
        turn_id: uuid.UUID | None,
        sandbox_image: str | None = None,
    ) -> tuple[dict, str]:
        """Per-topic sandbox (container + worktree + session + cheese env) + cwd.

        sandbox_image lets a project pick its own env image (e.g. cheesex-dev for
        dogfooding on this repo — spec §9.1 environment); falls back to the pool's
        default base image."""
        worktree = ws.topic_worktree(project_id, topic_id)
        resolved_image = sandbox_image or settings.sandbox_image
        container_name = ws.container_name(topic_id)
        self._warn_if_image_switch(topic_id, container_name, resolved_image)
        env = {
            "SBX_IMAGE": resolved_image,
            "SBX_CONTAINER": container_name,
            "SBX_WORKTREE": str(worktree),
            # The worktree is a jj workspace whose .jj/repo pointer is only
            # resolvable inside the sandbox if these are ALSO mounted (see
            # ws.sandbox_vcs_mounts) — the shim (claude-sbx) appends them to
            # `docker run` as extra `-v` args, space-joined since deterministic
            # workspace_root/UUID paths never contain whitespace.
            "SBX_VCS_MOUNTS": " ".join(ws.sandbox_vcs_mounts(project_id, topic_id)),
            "SBX_SESSION": str(ws.session_dir(project_id, topic_id)),
            "CHEESE_API": settings.agent_api_base(),
            "CHEESE_PROJECT": str(project_id),
            "CHEESE_TOPIC": str(topic_id),
            # Which 分身 this sandbox is (分身独立身份) — the same identity its
            # scoped CHEESE_TOKEN carries, never the shared account.
            "CHEESE_AUTHOR": topic_agent_handle(topic_id),
            # Per-turn token scoped to THIS project+topic (review R5): a container
            # for one project/topic can't write another's cheese endpoints.
            "CHEESE_TOKEN": mint_scoped_token(
                project_id=str(project_id), topic_id=str(topic_id)
            ),
        }
        if memory_scope:
            env["CHEESE_MEMORY_SCOPE"] = memory_scope
        if owner:
            env["CHEESE_OWNER"] = owner
        if turn_id:
            # cheese sends this back as X-Cheese-Turn so its blocks share the
            # turn's id (R4).
            env["CHEESE_TURN"] = str(turn_id)
        sandbox = {
            "cli_path": str(Path(settings.sandbox_shim).resolve()),
            "allowed_tools": _SANDBOX_TOOLS,
            "env": env,
        }
        return sandbox, str(worktree)

    def run_turn(
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
        # topic_id None (e.g. a project with no root topic) → no per-topic sandbox.
        if self.sandboxed() and topic_id is not None:
            sandbox, cwd = self._sandbox_config(
                project_id,
                topic_id,
                memory_scope=memory_scope,
                owner=owner,
                turn_id=turn_id,
                sandbox_image=sandbox_image,
            )
        else:
            sandbox, cwd = None, self._workspace_for(project_id)
        return self._agent.stream_reply(
            prompt=prompt,
            system_prompt=system_prompt,
            cwd=cwd,
            resume_session_id=resume_session_id,
            sandbox=sandbox,
            model=model,
            env=env,
            images=images,
        )

    async def deliver(
        self, topic_id: uuid.UUID, text: str, images: list[dict] | None = None
    ) -> bool:
        """No live screen to inject into: this provider runs the SDK per turn, so
        between turns there is no process to talk to and during one the turn owns
        the stream. The caller falls back to running its own turn."""
        del images
        return False

    def checkpoint(self, project_id: uuid.UUID, topic_id: uuid.UUID) -> None:
        """Snapshot the agent's native edits this turn into version history
        (workspace lifecycle, R2/R9). Best-effort; never fail the turn on git.

        Held while a `cheese await` command is still writing the worktree — the
        turn ends first BY DESIGN there, so this is the one moment the snapshot
        is guaranteed to catch a half-finished tree."""
        if not self.sandboxed():
            return
        awaited_tasks.checkpoint_worktree(project_id, topic_id)


class RemoteCheesedProvider:
    """Runs a turn on a remote cheesed node (design v2 R2/§5). Ships the request to
    the node's daemon and relays the AgentEvent stream back over NDJSON. The node's
    container calls cheese back to `cheese_api` with the per-turn scoped token the
    backend mints here — so execution runs anywhere while the platform stays the
    source of truth and the signing secret never leaves the backend."""

    name = "remote-cheesed"
    # The node holds the files and runs the same build_query_input on its side.
    embeds_images = True

    def __init__(self, *, cheesed_url: str, cheese_api: str):
        self._url = cheesed_url.rstrip("/")
        self._cheese_api = cheese_api

    def available(self) -> bool:
        return True

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
        body = {
            "project_id": str(project_id),
            "topic_id": str(topic_id),
            "prompt": prompt,
            "system_prompt": system_prompt,
            "resume_session_id": resume_session_id,
            "model": model or settings.agent_model,
            "env": env or {},
            # The node picks the project's env image (falls back to its own default).
            "sandbox_image": sandbox_image,
            "cheese_api": self._cheese_api,
            # Minted here (backend holds the signing secret); the node only relays it.
            "cheese_token": mint_scoped_token(
                project_id=str(project_id), topic_id=str(topic_id)
            ),
            "turn_id": str(turn_id) if turn_id else None,
            "memory_scope": memory_scope,
            "owner": owner,
            # 图片输入: worktree-relative image refs; the NODE (which has the
            # files) base64-embeds them into the user message (build_query_input).
            "images": images or [],
        }
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream(
                "POST", f"{self._url}/run-turn", json=body
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if line.strip():
                        yield event_from_dict(json.loads(line))

    async def deliver(
        self, topic_id: uuid.UUID, text: str, images: list[dict] | None = None
    ) -> bool:
        """Not relayed: the node's turn is an NDJSON stream this side consumes,
        with no back-channel into the running screen. Wiring one is a node RPC
        change, not something to fake here."""
        del images
        return False

    def checkpoint(self, project_id: uuid.UUID, topic_id: uuid.UUID) -> None:
        # Commit the turn's edits ON THE NODE so /git/log + /git/diff have history.
        # Best-effort; never fail the turn (runs after streaming, not in the path).
        #
        # The await hold is decided HERE rather than on the node: `cheese await`
        # registers with the platform, so this process is the only one that knows
        # a command is still writing that tree. There is no catch-up snapshot on
        # this path either — `_catch_up_snapshot` commits the LOCAL worktree, and
        # a remote node has none here — so the node catches up on the next turn's
        # checkpoint, which the report's wake provides.
        #
        # Known gap, accepted: two of the guards in `awaited_tasks` take the result
        # as a block WITHOUT waking (归档话题, 卡已结算). On those the node's tree
        # stays uncommitted until some later turn happens to run. Accepted because
        # both states mean nobody is reading that branch any more — but it is a
        # gap, not an invariant: do not read this as "a wake always follows".
        if awaited_tasks.snapshot_hold(topic_id) is not None:
            return
        try:
            httpx.post(f"{self._url}/checkpoint/{project_id}/{topic_id}", timeout=15)
        except httpx.HTTPError:
            pass


class ComputePool:
    """A pool of compute providers + per-turn selection (design §3 / v3).

    Mirror image of AIPool (profiles.ProfileRegistry): a registry with a default
    that is always available. Caps-matching + project quota + overflow queue land
    when there is more than one provider; today the default is returned directly.
    """

    def __init__(self, providers: list[ComputeProvider], default_name: str):
        self._providers = {p.name: p for p in providers}
        if default_name not in self._providers:
            raise ValueError(f"default provider {default_name!r} not registered")
        self._default_name = default_name

    @classmethod
    def local(
        cls, *, agent: AgentService, workspace_root: str, sandbox_enabled: bool
    ) -> "ComputePool":
        provider = LocalDockerProvider(
            agent=agent, workspace_root=workspace_root, sandbox_enabled=sandbox_enabled
        )
        return cls([provider], provider.name)

    def default(self) -> ComputeProvider:
        return self._providers[self._default_name]

    @classmethod
    def remote(cls, *, cheesed_url: str, cheese_api: str) -> "ComputePool":
        provider = RemoteCheesedProvider(cheesed_url=cheesed_url, cheese_api=cheese_api)
        return cls([provider], provider.name)

    @classmethod
    def tmux(
        cls, *, image: str, idle_suspect_s: float, hard_ceiling_s: float
    ) -> "ComputePool":
        """Interactive/tmux backend (AGENT_BACKEND=tmux): drives `claude` in a
        tmux session and streams events from Claude Code HTTP hooks."""
        from app.domain.agent.tmux_provider import TmuxHooksProvider

        provider = TmuxHooksProvider(
            image=image, idle_suspect_s=idle_suspect_s, hard_ceiling_s=hard_ceiling_s
        )
        return cls([provider], provider.name)

    @classmethod
    def device(cls, *, idle_suspect_s: float, hard_ceiling_s: float) -> "ComputePool":
        """Self-hosted / BYO backend (AGENT_BACKEND=device, P3): runs the turn on a
        user's own enrolled machine via the frozen link.Msg channel, streaming events
        from Claude Code hooks — same contract, execution relocated to the device.

        The two-layer timeout is the SAME policy the local tmux backend runs
        (turn 活跃度检测): `idle_suspect_s` then a `_confirm_alive` process-tree
        probe, `hard_ceiling_s` as the backstop."""
        from app.domain.agent.device_provider import DeviceProvider

        provider = DeviceProvider(
            idle_suspect_s=idle_suspect_s, hard_ceiling_s=hard_ceiling_s
        )
        return cls([provider], provider.name)

    def tmux_activity_status(self, topic_id: uuid.UUID) -> dict | None:
        """turn 活跃度检测: `cheese status`'s idle-suspect signal, read from
        whichever tmux provider is in this pool (at most one — see
        `build_compute_pool`). None when there's no tmux provider in the pool,
        or no turn currently monitored for this topic (not running, or running
        on a different backend)."""
        from app.domain.agent.tmux_provider import TmuxHooksProvider

        for provider in self._providers.values():
            if isinstance(provider, TmuxHooksProvider):
                return provider.activity_status(topic_id)
        return None

    async def deliver(
        self, topic_id: uuid.UUID, text: str, images: list[dict] | None = None
    ) -> bool:
        """Inject text into whichever provider is currently running a turn on
        this topic. Asks every provider rather than resolving the topic's
        configured one: only a provider that HAS a live screen for this exact
        topic can answer True, so the first True is the right one — and it needs
        no DB read on the hot path where a human is waiting."""
        for provider in self._providers.values():
            delivered = (
                await provider.deliver(topic_id, text, images=images)
                if images
                else await provider.deliver(topic_id, text)
            )
            if delivered:
                return True
        return False

    def bind_hook_event_consumer(
        self,
        consumer: "HookEventConsumer",
        activity_consumer: "HookActivityConsumer | None" = None,
    ) -> None:
        """Give hooks providers the room-side persistence and activity owners."""
        from app.domain.agent.harness.claude_code import (
            HooksSessionProvider,
        )

        for provider in self._providers.values():
            if isinstance(provider, HooksSessionProvider):
                provider.bind_event_consumer(consumer)
                if activity_consumer is not None:
                    provider.bind_activity_consumer(activity_consumer)

    def bind_prompt_receipt_consumer(
        self, consumer: Callable[[uuid.UUID, str], Awaitable[None]]
    ) -> None:
        """Give hooks providers the owner of UserPromptSubmit receipts — the
        consumed-stamp side of #539 decision A."""
        from app.domain.agent.harness.claude_code import (
            HooksSessionProvider,
        )

        for provider in self._providers.values():
            if isinstance(provider, HooksSessionProvider):
                provider.bind_receipt_consumer(consumer)

    def has_live_screen(self, topic_id: uuid.UUID) -> bool:
        """Does any provider in this pool still hold a screen for this topic?
        See `HooksSessionProvider.has_live_screen`."""
        from app.domain.agent.harness.claude_code import (
            HooksSessionProvider,
        )

        return any(
            provider.has_live_screen(topic_id)
            for provider in self._providers.values()
            if isinstance(provider, HooksSessionProvider)
        )

    async def recover_hook_subscriptions(
        self, device_id: str | None = None
    ) -> list["TopicSubscription"]:
        """Recover subscriptions for screens that survived this process."""
        from app.domain.agent.harness.claude_code import (
            HooksSessionProvider,
        )

        recovered: list[TopicSubscription] = []
        for provider in self._providers.values():
            if isinstance(provider, HooksSessionProvider):
                recovered.extend(await provider.recover_subscriptions(device_id))
        return recovered

    def has(self, provider_id: str) -> bool:
        return provider_id in self._providers

    def select(
        self, *, provider_id: str | None = None, env_spec: dict | None = None
    ) -> ComputeProvider:
        """Pick a provider for this turn (execution-architecture v4 会话级选择).

        ``provider_id`` is the compute a topic/project chose (resolved upstream from
        ``topic.compute_profile`` → project sticky). A registered id routes the turn
        to that provider; an unknown / None id falls back to the pool default (which
        is always available) — so a stored selection that isn't deployed here never
        breaks a turn. caps/quota/queue routing arrives with ``env_spec`` (design §3
        pick_provider, v2 R9)."""
        if provider_id is not None and provider_id in self._providers:
            return self._providers[provider_id]
        return self.default()


def build_compute_pool(
    agent: AgentService, *, cloud_provider: ComputeProvider | None = None
) -> ComputePool:
    """Build the ComputePool from settings.

    ``agent_backend`` picks the LOCAL transport — how a turn reaches a container
    on this box — and nothing else. It used to pick the whole pool, which made
    the convergence end state unreachable: fusion-design §8.6 settles on ONE turn
    flow with two thin transports (tmux locally, device remotely, a topic
    choosing per turn), and returning a single-provider pool for `tmux` dropped
    the remote transport entirely. The only way to have device compute was to run
    the pre-convergence SDK path locally — a configuration nobody chose.

    So: exactly one local provider joins the pool (tmux or the SDK's
    LocalDockerProvider), the device transport ALWAYS joins it, and a remote
    cheesed node joins when wired.
    """
    local: ComputeProvider
    if settings.agent_backend == "tmux":
        # Interactive `claude` in a per-topic tmux session in a platform
        # container, driven by docker exec + send-keys (fusion-design §8.6: the
        # LOCAL transport of the hooks substrate).
        from app.domain.agent.tmux_provider import TmuxHooksProvider

        local = TmuxHooksProvider(
            image=settings.tmux_sandbox_image,
            idle_suspect_s=settings.agent_idle_suspect_s,
            hard_ceiling_s=settings.agent_turn_hard_ceiling_s,
        )
    else:
        # The pre-convergence SDK stream-json path, retained as the orthogonal
        # product form (§8.6 item 4) rather than the main line.
        local = LocalDockerProvider(
            agent=agent,
            workspace_root=settings.workspace_root,
            sandbox_enabled=settings.agent_sandbox_enabled,
        )
    providers: list[ComputeProvider] = [local]
    # The remote transport ALWAYS joins, whatever the local one is: a topic picks
    # its compute per turn (with topic affinity — the pin freezes on the first
    # turn), and it is only offered when a device is actually online (gated in the
    # market listing), so this stays opt-in.
    from app.domain.agent.device_provider import DeviceProvider

    # Same two-layer timeout policy as the local tmux backend (turn 活跃度检测),
    # from the SAME settings — the local and remote hooks backends share one knob
    # pair, they don't drift. Replaces the old single `device_turn_timeout_s` that
    # collapsed both layers into one 900s deadline and killed long-but-silent turns.
    providers.append(
        DeviceProvider(
            idle_suspect_s=settings.agent_idle_suspect_s,
            hard_ceiling_s=settings.agent_turn_hard_ceiling_s,
        )
    )
    if cloud_provider is not None:
        providers.append(cloud_provider)
    if settings.cheesed_url:
        providers.append(
            RemoteCheesedProvider(
                cheesed_url=settings.cheesed_url,
                cheese_api=settings.cheesed_cheese_api,
            )
        )
    if settings.agent_backend == "device":
        # Every turn on someone else's machine unless a topic says otherwise.
        default_name = DeviceProvider.name
    elif settings.compute_provider == "remote" and settings.cheesed_url:
        default_name = RemoteCheesedProvider.name
    else:
        default_name = local.name
    return ComputePool(providers, default_name)


def app_preview_reachable(compute_profile: str | None) -> bool:
    """Can 运行环境预览 exist for a topic running on this compute at all?

    The feature resolves the app port a *docker container on the backend's own
    host* publishes (``workspace.app_endpoint`` → ``docker port``). That mapping
    exists only when the topic's box IS a container here. A turn running on
    someone's enrolled machine (``device``) or on a leased Cloud machine
    (``cloud`` — a DeviceProvider subclass) has no container on this host, so the
    lookup returns None for a reason that has nothing to do with the app: there
    is no path from the platform to that port, and there never was.

    Without this distinction both cases collapse into "container down", and the
    panel tells those users to @ 芝士 again to bring up a box that is not coming.
    """
    from app.domain.agent.market import compute_default_name
    from app.domain.agent.tmux_provider import TmuxHooksProvider

    local_box = {TmuxHooksProvider.name, LocalDockerProvider.name}
    # A topic that has an app artifact has necessarily run a turn, and the first
    # turn pins `topic.compute_profile` — so the sticky project/team chain is
    # already collapsed into it and only the deployment default is left to apply.
    return (compute_profile or compute_default_name()) in local_box
