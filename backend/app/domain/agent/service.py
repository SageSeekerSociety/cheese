"""Claude Agent SDK integration — 芝士 (spec §8, §9).

Wraps `claude-agent-sdk` to run a resumable, streaming conversation per topic.
We do NOT parse the model's natural-language output (spec §9.1); the platform
observes the agent through structured SDK messages only.

Key SDK facts (verified against installed claude-agent-sdk 0.2.x):
- `StreamEvent.event` carries Anthropic-style streaming deltas when
  `include_partial_messages=True` — we surface `text_delta`s for live UI.
- `AssistantMessage` text blocks are the authoritative final text we persist.
- `ResultMessage.session_id` is the token used to resume the conversation.
"""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    StreamEvent,
    TextBlock,
    ToolUseBlock,
)


@dataclass
class AgentDelta:
    """A streamed token chunk for live display."""

    text: str


@dataclass
class AgentToolUse:
    """A platform tool 芝士 invoked (for 施工现场 observability)."""

    name: str
    input: dict[str, Any]


@dataclass
class AgentUsage:
    """Token/cost accounting for one turn (spec §9.1/§10.2)."""

    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass
class AgentResult:
    """Authoritative final reply plus the session id to resume next time."""

    text: str
    session_id: str | None
    usage: AgentUsage | None = None


AgentEvent = AgentDelta | AgentToolUse | AgentResult


def _extract_text_delta(event: dict) -> str | None:
    """Pull text from an Anthropic streaming event dict, if present."""
    if event.get("type") != "content_block_delta":
        return None
    delta = event.get("delta") or {}
    if delta.get("type") == "text_delta":
        text = delta.get("text")
        return text if isinstance(text, str) else None
    return None


def _assistant_text(message: AssistantMessage) -> str:
    return "".join(
        block.text for block in message.content if isinstance(block, TextBlock)
    )


# Claude Code's built-in tools. 芝士 must act only through platform (cheese) MCP
# tools (spec §9.1), so we disallow the built-ins — no arbitrary Bash/file I/O.
_BUILTIN_TOOLS = [
    "Bash",
    "BashOutput",
    "KillShell",
    "Read",
    "Write",
    "Edit",
    "NotebookEdit",
    "Glob",
    "Grep",
    "WebFetch",
    "WebSearch",
    "Task",
    "TodoWrite",
]


class AgentService:
    """Runs one streaming turn against the Claude Agent SDK."""

    def __init__(self, *, model: str, env: dict[str, str] | None = None):
        self._model = model
        self._env = env or {}

    async def stream_reply(
        self,
        *,
        prompt: str,
        system_prompt: str,
        cwd: str,
        resume_session_id: str | None,
        mcp_servers: dict[str, Any] | None = None,
        allowed_tools: list[str] | None = None,
        sandbox: dict[str, Any] | None = None,
    ) -> AsyncIterator[AgentEvent]:
        """Yield AgentDelta chunks (and AgentToolUse events) live, then a final
        AgentResult.

        Two modes:
        - sandbox given: run `claude` INSIDE a per-topic container via the cli_path
          shim, with NATIVE tools (Bash/Read/Write/Edit jailed by the container)
          and platform actions via the in-container `cheese` CLI (spec §9.1).
        - otherwise: in-process MCP platform tools, host built-ins disallowed.
        """
        if sandbox:
            options = ClaudeAgentOptions(
                model=self._model,
                system_prompt=system_prompt,
                cwd=cwd,
                resume=resume_session_id,
                include_partial_messages=True,
                permission_mode="bypassPermissions",
                allowed_tools=sandbox["allowed_tools"],
                cli_path=sandbox["cli_path"],
                # The cheese skill lives in the mounted ~/.claude/skills (=user
                # source). "user" reads only the isolated per-topic session dir
                # in the container, so no host settings leak in.
                setting_sources=["user"],
                skills=["cheese"],
                env={**self._env, **sandbox["env"]},
            )
        else:
            options = ClaudeAgentOptions(
                model=self._model,
                system_prompt=system_prompt,
                cwd=cwd,
                resume=resume_session_id,
                include_partial_messages=True,
                permission_mode="bypassPermissions",
                allowed_tools=allowed_tools or [],
                disallowed_tools=_BUILTIN_TOOLS,  # only platform tools (spec §9.1)
                mcp_servers=mcp_servers or {},
                setting_sources=[],  # isolate from the host's ~/.claude settings
                env=self._env,
            )

        final_text = ""
        session_id = resume_session_id
        usage = AgentUsage(model=self._model)

        async with ClaudeSDKClient(options=options) as client:
            await client.query(prompt)
            async for message in client.receive_response():
                if isinstance(message, StreamEvent):
                    text = _extract_text_delta(message.event)
                    if text:
                        yield AgentDelta(text=text)
                elif isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, ToolUseBlock):
                            yield AgentToolUse(
                                name=block.name,
                                input=block.input
                                if isinstance(block.input, dict)
                                else {},
                            )
                    final_text = _assistant_text(message) or final_text
                    if message.session_id:
                        session_id = message.session_id
                elif isinstance(message, ResultMessage):
                    session_id = message.session_id or session_id
                    if not final_text and message.result:
                        final_text = message.result
                    if message.total_cost_usd:
                        usage.cost_usd = message.total_cost_usd
                    u = message.usage or {}
                    if isinstance(u, dict):
                        usage.input_tokens = int(u.get("input_tokens", 0) or 0)
                        usage.output_tokens = int(u.get("output_tokens", 0) or 0)

        yield AgentResult(text=final_text, session_id=session_id, usage=usage)
