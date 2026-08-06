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

import asyncio
import json
import subprocess
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Protocol

import httpx

from app.core.config import settings
from app.core.sandbox_auth import mint_scoped_token
from app.domain.agent.sandbox_notices import warn_image_switch_rebuild
from app.domain.agent.service import (
    AgentEvent,
    AgentService,
    event_from_dict,
)
from app.domain.workspace import service as ws

# Author handle for 芝士's cheese-CLI callbacks (kept here to avoid importing
# chat.py, which imports this module).
_CHEESE_AUTHOR = "cheese"

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

    def checkpoint(self, project_id: uuid.UUID, topic_id: uuid.UUID) -> None: ...


class LocalDockerProvider:
    """Default provider: builds the local per-topic Docker sandbox and runs the
    SDK in-process. When Docker is absent (tests / degraded) it runs a plain
    model turn with no platform tools — same behaviour as before, now owned here
    instead of in ChatService."""

    name = "local-docker"

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
        try:
            asyncio.get_running_loop().create_task(warn_image_switch_rebuild(topic_id))
        except RuntimeError:
            pass  # no running loop — nothing to schedule onto

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
            "SBX_VCS_MOUNTS": " ".join(
                ws.sandbox_vcs_mounts(project_id, ws.branch_for_topic(topic_id))
            ),
            "SBX_SESSION": str(ws.session_dir(project_id, topic_id)),
            "CHEESE_API": settings.sandbox_api_base,
            "CHEESE_PROJECT": str(project_id),
            "CHEESE_TOPIC": str(topic_id),
            "CHEESE_AUTHOR": _CHEESE_AUTHOR,
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

    def checkpoint(self, project_id: uuid.UUID, topic_id: uuid.UUID) -> None:
        """Snapshot the agent's native edits this turn into version history
        (workspace lifecycle, R2/R9). Best-effort; never fail the turn on git."""
        if not self.sandboxed():
            return
        try:
            ws.snapshot_worktree(project_id, topic_id)
        except Exception:  # noqa: BLE001 — git snapshot is best-effort
            pass


class RemoteCheesedProvider:
    """Runs a turn on a remote cheesed node (design v2 R2/§5). Ships the request to
    the node's daemon and relays the AgentEvent stream back over NDJSON. The node's
    container calls cheese back to `cheese_api` with the per-turn scoped token the
    backend mints here — so execution runs anywhere while the platform stays the
    source of truth and the signing secret never leaves the backend."""

    name = "remote-cheesed"

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

    def checkpoint(self, project_id: uuid.UUID, topic_id: uuid.UUID) -> None:
        # Commit the turn's edits ON THE NODE so /git/log + /git/diff have history.
        # Best-effort; never fail the turn (runs after streaming, not in the path).
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
    def tmux(cls, *, image: str, turn_timeout_s: float) -> "ComputePool":
        """Interactive/tmux backend (AGENT_BACKEND=tmux): drives `claude` in a
        tmux session and streams events from Claude Code HTTP hooks."""
        from app.domain.agent.tmux_provider import TmuxHooksProvider

        provider = TmuxHooksProvider(image=image, turn_timeout_s=turn_timeout_s)
        return cls([provider], provider.name)

    @classmethod
    def device(cls, *, turn_timeout_s: float) -> "ComputePool":
        """Self-hosted / BYO backend (AGENT_BACKEND=device, P3): runs the turn on a
        user's own enrolled machine via the frozen link.Msg channel, streaming events
        from Claude Code hooks — same contract, execution relocated to the device."""
        from app.domain.agent.device_provider import DeviceProvider

        provider = DeviceProvider(turn_timeout_s=turn_timeout_s)
        return cls([provider], provider.name)

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


def build_compute_pool(agent: AgentService) -> ComputePool:
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
            turn_timeout_s=settings.agent_turn_timeout_s,
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

    providers.append(DeviceProvider(turn_timeout_s=settings.device_turn_timeout_s))
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
