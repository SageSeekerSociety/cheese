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

import base64
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    RateLimitEvent,
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
class AgentMessage:
    """One COMPLETED top-level assistant message (the SDK's AssistantMessage
    boundary — a STRUCTURAL event, never parsed out of prose). A turn with tool
    calls yields several of these; each becomes its own chat message block
    (Slack-style discrete messages instead of one growing streamed bubble)."""

    text: str
    # Stable per-event id on the hooks path (see AgentToolUse.eid) so the spool
    # reconcile can dedup a backfilled message against its live delivery.
    eid: str | None = None


@dataclass
class AgentToolUse:
    """A platform tool 芝士 invoked (for 施工现场 observability)."""

    name: str
    input: dict[str, Any]
    # Stable per-event id (the hook forwarder's X-Cheese-Event-Id / spool filename).
    # Lets the durable-spool reconcile dedup a backfilled 现场 event against the one
    # the live hook path already persisted. None off the hooks path (sdk backend).
    eid: str | None = None


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
class AgentSessionInfo:
    """Yielded as soon as the CLI announces the session id — BEFORE the final
    result — so even a turn that dies mid-stream can persist the pointer, and
    '再 @ 一次接着做' truly RESUMES the partial work instead of replaying."""

    session_id: str


@dataclass
class AgentResult:
    """Authoritative final reply plus the session id to resume next time.

    is_error mirrors the SDK ResultMessage's structured flag: the run ended in a
    provider/infra failure (e.g. seat rate-limit) and `text` is that failure's
    detail — NOT something 芝士 said."""

    text: str
    session_id: str | None
    usage: AgentUsage | None = None
    is_error: bool = False
    # Structured failure context (no text sniffing): the failing API call's HTTP
    # status, the CLI's error strings, and — when the seat rate-limit tripped —
    # the RateLimitInfo dict (status / resets_at unix timestamp / type).
    api_error_status: int | None = None
    errors: list[str] | None = None
    rate_limit: dict | None = None


AgentEvent = AgentDelta | AgentMessage | AgentToolUse | AgentSessionInfo | AgentResult


def event_to_dict(event: AgentEvent) -> dict:
    """Serialize an AgentEvent for the wire (backend ⇄ cheesed node, design v2 R2)."""
    if isinstance(event, AgentDelta):
        return {"t": "delta", "text": event.text}
    if isinstance(event, AgentMessage):
        return {"t": "message", "text": event.text, "eid": event.eid}
    if isinstance(event, AgentToolUse):
        return {"t": "tool", "name": event.name, "input": event.input, "eid": event.eid}
    if isinstance(event, AgentSessionInfo):
        return {"t": "session", "session_id": event.session_id}
    usage = event.usage
    return {
        "t": "result",
        "text": event.text,
        "session_id": event.session_id,
        "is_error": event.is_error,
        "api_error_status": event.api_error_status,
        "errors": event.errors,
        "rate_limit": event.rate_limit,
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
    if kind == "message":
        return AgentMessage(text=d.get("text", ""), eid=d.get("eid"))
    if kind == "tool":
        return AgentToolUse(
            name=d.get("name", ""), input=d.get("input") or {}, eid=d.get("eid")
        )
    if kind == "session":
        return AgentSessionInfo(session_id=d.get("session_id", ""))
    u = d.get("usage")
    return AgentResult(
        text=d.get("text", ""),
        session_id=d.get("session_id"),
        usage=None if u is None else AgentUsage(**u),
        is_error=bool(d.get("is_error", False)),
        api_error_status=d.get("api_error_status"),
        errors=d.get("errors"),
        rate_limit=d.get("rate_limit"),
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


# Per-image ceiling for NATIVE image input (图片输入): the Anthropic API rejects
# images over ~5MB base64, and base64 inflates raw bytes by 4/3 — so cap raw
# size at 3.75MB. Bigger files fall back to a text note pointing at the
# worktree path (sandbox Read is image-capable, so 芝士 can still open it).
_IMAGE_MAX_BYTES = 3_750_000


def _image_content_block(cwd: str, path: str, media_type: str) -> dict | None:
    """Base64 image block for one worktree image, or None if the file is
    missing, escapes the worktree, or exceeds the API's per-image size cap."""
    try:
        root = Path(cwd).resolve()
        target = (root / path).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            return None
        if target.stat().st_size > _IMAGE_MAX_BYTES:
            return None
        data = base64.standard_b64encode(target.read_bytes()).decode("ascii")
    except OSError:
        return None
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": media_type, "data": data},
    }


def build_query_input(
    prompt: str, images: list[dict] | None, cwd: str
) -> str | list[dict]:
    """What we hand to `client.query()`: the plain prompt string, or — when the
    turn carries images (图片输入) — Anthropic-style content blocks with each
    image embedded natively (base64), so 芝士 SEES them in the message itself
    (Claude Code native image input) instead of having to Read files.

    Images that can't be embedded (too big / unreadable) degrade to a text note
    pointing at the worktree path."""
    if not images:
        return prompt
    image_blocks: list[dict] = []
    notes: list[str] = []
    for img in images:
        path = str(img.get("path") or "")
        media_type = str(img.get("media_type") or "") or "image/png"
        block = _image_content_block(cwd, path, media_type)
        if block is not None:
            image_blocks.append(block)
        elif path:
            notes.append(
                f"（图片 {path} 未能随消息附上——太大或暂不可读；"
                "可用 Read 工具打开这个工作区文件查看）"
            )
    if not image_blocks and not notes:
        return prompt
    text = "\n".join(s for s in (prompt, *notes) if s)
    blocks: list[dict] = []
    if text:
        blocks.append({"type": "text", "text": text})
    blocks.extend(image_blocks)
    return blocks


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
        images: list[dict] | None = None,
    ) -> AsyncIterator[AgentEvent]:
        """Yield AgentDelta chunks (and AgentToolUse events) live, then a final
        AgentResult.

        `images` (图片输入): worktree images this turn carries, each
        {"path": <cwd-relative>, "media_type": <mime>} — embedded NATIVELY as
        base64 image blocks in the user message (see build_query_input), so the
        model sees them without any tool round-trip.

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
                # "project" makes the CLI natively auto-load the workspace's own
                # CLAUDE.md / .claude settings, so a user repo's conventions reach
                # the agent (decision: 方案 A, topic CLAUDE.md处理策略).
                setting_sources=["user", "project"],
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
        result_error = False
        api_error_status: int | None = None
        cli_errors: list[str] | None = None
        rate_limit: dict | None = None

        query_input = build_query_input(prompt, images, cwd)
        if isinstance(query_input, str):
            request: Any = query_input
        else:
            # Streaming-input form: one user message whose content is a block
            # list (text + native base64 image blocks) — the SDK/CLI accept
            # Anthropic-style content arrays here.
            async def _one_message() -> AsyncIterator[dict]:
                yield {
                    "type": "user",
                    "message": {"role": "user", "content": query_input},
                    "parent_tool_use_id": None,
                }

            request = _one_message()

        # With include_partial_messages=True the SDK can emit several top-level
        # AssistantMessages for one logical prose segment. They are transport
        # fragments: persisting each one independently breaks Markdown that spans
        # them (``` / body / ``` became two empty code boxes in production).
        # A tool call or the final ResultMessage is the real semantic boundary.
        pending_text = ""

        async with ClaudeSDKClient(options=options) as client:
            await client.query(request)
            try:
                async for message in client.receive_response():
                    if isinstance(message, StreamEvent):
                        text = _extract_text_delta(message.event)
                        if text:
                            yield AgentDelta(text=text)
                    elif isinstance(message, AssistantMessage):
                        # Preserve block order: prose before a tool belongs to
                        # the completed chat message immediately preceding it.
                        for block in message.content:
                            if isinstance(block, TextBlock):
                                if message.parent_tool_use_id is None:
                                    pending_text += block.text
                            elif isinstance(block, ToolUseBlock):
                                if pending_text.strip():
                                    yield AgentMessage(text=pending_text)
                                    final_text = pending_text
                                    pending_text = ""
                                yield AgentToolUse(
                                    name=block.name,
                                    input=(
                                        block.input
                                        if isinstance(block.input, dict)
                                        else {}
                                    ),
                                )
                        # Only TOP-LEVEL messages are 芝士 speaking to the user;
                        # subagent prose is intentionally ignored above. Tool
                        # calls still surface for the work log.
                        if message.session_id:
                            if message.session_id != session_id:
                                yield AgentSessionInfo(session_id=message.session_id)
                            session_id = message.session_id
                    elif isinstance(message, RateLimitEvent):
                        # Emitted on status transitions; `rejected` + resets_at is
                        # the structured form of "You've hit your session limit".
                        info = message.rate_limit_info
                        rate_limit = {
                            "status": info.status,
                            "resets_at": info.resets_at,
                            "type": info.rate_limit_type,
                            "utilization": info.utilization,
                        }
                    elif isinstance(message, ResultMessage):
                        if pending_text.strip():
                            yield AgentMessage(text=pending_text)
                            final_text = pending_text
                            pending_text = ""
                        session_id = message.session_id or session_id
                        result_error = bool(message.is_error)
                        api_error_status = message.api_error_status
                        cli_errors = message.errors
                        if not final_text and message.result:
                            final_text = message.result
                        if message.total_cost_usd:
                            usage.cost_usd = message.total_cost_usd
                        u = message.usage or {}
                        if isinstance(u, dict):
                            usage.input_tokens = int(u.get("input_tokens", 0) or 0)
                            usage.output_tokens = int(u.get("output_tokens", 0) or 0)
            except Exception:
                # A provider failure before result/tool must not erase prose the
                # user already saw streaming. Yield it once so the orchestration
                # layer persists it, then preserve the original failure.
                if pending_text.strip():
                    yield AgentMessage(text=pending_text)
                raise

        yield AgentResult(
            text=final_text,
            session_id=session_id,
            usage=usage,
            is_error=result_error,
            api_error_status=api_error_status,
            errors=cli_errors,
            rate_limit=rate_limit,
        )
