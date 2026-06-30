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

import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Protocol

from app.core.config import settings
from app.core.sandbox_auth import mint_scoped_token
from app.domain.agent.service import AgentEvent, AgentService
from app.domain.workspace import service as ws

# Author handle for 芝士's cheese-CLI callbacks (kept here to avoid importing
# chat.py, which imports this module).
_CHEESE_AUTHOR = "cheese"

# Native tools 芝士 may use inside the sandbox + the Task tools (live todo).
_SANDBOX_TOOLS = [
    "Bash", "Read", "Write", "Edit", "Grep", "Glob",
    "TaskCreate", "TaskUpdate", "TaskList", "TaskGet",
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

    def _sandbox_config(
        self,
        project_id: uuid.UUID,
        topic_id: uuid.UUID,
        *,
        memory_scope: str | None,
        owner: str | None,
    ) -> tuple[dict, str]:
        """Per-topic sandbox (container + worktree + session + cheese env) + cwd."""
        worktree = ws.topic_worktree(project_id, topic_id)
        env = {
            "SBX_IMAGE": settings.sandbox_image,
            "SBX_CONTAINER": ws.container_name(topic_id),
            "SBX_WORKTREE": str(worktree),
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
    ) -> AsyncIterator[AgentEvent]:
        # topic_id None (e.g. a project with no root topic) → no per-topic sandbox.
        if self.sandboxed() and topic_id is not None:
            sandbox, cwd = self._sandbox_config(
                project_id, topic_id, memory_scope=memory_scope, owner=owner
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

    def select(self, *, env_spec: dict | None = None) -> ComputeProvider:
        """Pick a provider for this turn. Single-provider today → the default
        (always available); caps/quota/queue routing arrives with more providers
        (design §3 pick_provider, v2 R9)."""
        return self.default()
