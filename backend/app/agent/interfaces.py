"""The abstract Agent contract (architecture doc §6, the "编排器 <-> 适配器" seam).

The orchestrator drives an agent only through these interfaces; swapping the
underlying agent framework means writing a new adapter, leaving tools/business
untouched.

Crucially, an agent is a **text-in, tools-out** abstraction: the orchestrator
*delivers text* to it (``AgentSession.send``) and controls its lifecycle
(start/stop), but the agent's *output* is not returned here — it flows
out-of-band as structured tool calls that hit the backend's ToolInvoker. So
this contract deliberately has no "receive output" method.
"""

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from app.agent.authorization.authorizer import ProjectActor


@dataclass(frozen=True)
class AgentContext:
    """Everything an adapter needs to run one agent.

    ``actor`` is the injected identity/authz principal (never agent-supplied);
    ``role_prompt`` is the prompt-conveyed responsibility (power, not
    permission); ``settings`` carries adapter-specific config (cwd, model, ...).
    """

    actor: ProjectActor
    role_prompt: str = ""
    settings: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class AgentSession(Protocol):
    """A handle to one running agent session."""

    @property
    def session_id(self) -> str: ...

    async def send(self, text: str) -> None:
        """Deliver text (a message / instruction) to the agent's input."""
        ...

    async def stop(self) -> None:
        """Stop the session; idempotent."""
        ...

    async def is_alive(self) -> bool: ...


@runtime_checkable
class Agent(Protocol):
    """An adapter that can start agent sessions (cheesed_codex, naive_api, ...)."""

    async def start(self, context: AgentContext) -> AgentSession:
        """Start a session for the agent described by ``context``."""
        ...
