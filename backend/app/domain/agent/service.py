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

from app.domain.agent.skills import load_cheese_cli_rules

# The cheese CLI rules, injected into every sandbox turn's system prompt (the
# cheese Agent Skill is lazy-loaded and weak models don't self-load it). Read
# once at import; editing SKILL.md takes effect on the next backend restart.
_CHEESE_RULES = load_cheese_cli_rules()


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


def event_to_dict(event: AgentEvent) -> dict:
    """Serialize an AgentEvent for the wire (backend ⇄ cheesed node, design v2 R2)."""
    if isinstance(event, AgentDelta):
        return {"t": "delta", "text": event.text}
    if isinstance(event, AgentToolUse):
        return {"t": "tool", "name": event.name, "input": event.input}
    usage = event.usage
    return {
        "t": "result",
        "text": event.text,
        "session_id": event.session_id,
        "usage": None
        if usage is None
        else {
            "model": usage.model,
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "cost_usd": usage.cost_usd,
        },
    }


def event_from_dict(d: dict) -> AgentEvent:
    """Inverse of event_to_dict."""
    kind = d.get("t")
    if kind == "delta":
        return AgentDelta(text=d.get("text", ""))
    if kind == "tool":
        return AgentToolUse(name=d.get("name", ""), input=d.get("input") or {})
    u = d.get("usage")
    return AgentResult(
        text=d.get("text", ""),
        session_id=d.get("session_id"),
        usage=None if u is None else AgentUsage(**u),
    )


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
        sandbox: dict[str, Any] | None = None,
        model: str | None = None,
        env: dict[str, str] | None = None,
    ) -> AsyncIterator[AgentEvent]:
        """Yield AgentDelta chunks (and AgentToolUse events) live, then a final
        AgentResult.

        - sandbox given: run `claude` INSIDE a per-topic container via the cli_path
          shim, with NATIVE tools (Bash/Read/Write/Edit jailed by the container)
          and platform actions via the in-container `cheese` CLI (spec §9.1).
        - otherwise (no Docker / tests): plain model turn with built-ins disallowed
          and no platform tools.

        `model`/`env` override this turn's provider (per-project ExecutionProfile,
        design §2); they default to the service's own model/env.
        """
        eff_model = model or self._model
        eff_env = env if env is not None else self._env
        if sandbox:
            # cheese CLI rules go straight into the system prompt: Agent Skills only
            # preload name+description, and weak gateway models don't reliably do the
            # self-directed read that loads the body — but cheese is needed every turn
            # (see load_cheese_cli_rules). We keep the skill enabled too (capable
            # models can still self-load it), which is why "Skill" must be in the
            # explicit allowed_tools — when you pass allowed_tools yourself, the SDK
            # does NOT auto-add Skill, so omitting it silently blocks skill invocation.
            sandbox_prompt = system_prompt
            if _CHEESE_RULES:
                sandbox_prompt = f"{system_prompt}\n\n{_CHEESE_RULES}"
            options = ClaudeAgentOptions(
                model=eff_model,
                system_prompt=sandbox_prompt,
                cwd=cwd,
                resume=resume_session_id,
                include_partial_messages=True,
                permission_mode="bypassPermissions",
                allowed_tools=[*sandbox["allowed_tools"], "Skill"],
                cli_path=sandbox["cli_path"],
                # The cheese skill lives in the mounted ~/.claude/skills (=user
                # source). "user" reads only the isolated per-topic session dir
                # in the container, so no host settings leak in.
                setting_sources=["user"],
                skills=["cheese"],
                env={**eff_env, **sandbox["env"]},
            )
        else:
            options = ClaudeAgentOptions(
                model=eff_model,
                system_prompt=system_prompt,
                cwd=cwd,
                resume=resume_session_id,
                include_partial_messages=True,
                permission_mode="bypassPermissions",
                allowed_tools=[],
                disallowed_tools=_BUILTIN_TOOLS,
                setting_sources=[],  # isolate from the host's ~/.claude settings
                env=eff_env,
            )

        final_text = ""
        session_id = resume_session_id
        usage = AgentUsage(model=eff_model)

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
