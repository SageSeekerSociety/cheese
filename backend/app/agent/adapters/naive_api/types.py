"""Model-facing types for the naive_api adapter.

The adapter depends on an abstract ``ChatModel`` rather than a concrete client,
so the agentic loop is testable with a scripted fake and the real binding
(AsyncOpenAI tool-calling, reusing settings.openai_*) is a thin, swappable
implementation.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.agent.authorization.authorizer import ProjectActor

# Executes one tool call for an actor and returns its result. In production this
# wraps ToolInvoker (opening a db session); in tests it is a fake.
ToolExecutor = Callable[[ProjectActor, str, dict[str, Any]], Awaitable[Any]]


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    args: dict[str, Any]


@dataclass(frozen=True)
class ModelResponse:
    """One model turn: optional text plus zero or more tool calls. An empty
    ``tool_calls`` means the model is done for this input."""

    text: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)


class ChatModel(Protocol):
    async def respond(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> ModelResponse: ...
