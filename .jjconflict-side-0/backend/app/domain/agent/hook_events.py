"""Claude Code hooks → AgentEvent (tmux backend).

The interactive `claude` running in a tmux session emits structured events via
Claude Code hooks. (Command hooks, not HTTP: Claude Code blocks HTTP hooks to
non-loopback targets, so a baked `cheese-hook` script reads the hook JSON on
stdin and POSTs it to /sandbox/hooks/{topic}.) This module is the pure,
docker-free core of the tmux backend:

- ``translate_hook`` maps ONE hook payload to an AgentEvent (spec §9.1: the
  platform observes 芝士 through structured events, never by parsing prose).
- ``HookRouter`` fans hook POSTs (from the /sandbox/hooks endpoint) to the
  long-lived sink owned by that topic's interactive screen.

Event mapping (verified in the spike, docs/tmux-backend-spike.md):
  SessionStart{session_id}                → AgentSessionInfo
  PreToolUse{tool_name, tool_input}       → AgentToolUse
  MessageDisplay{delta} (non-empty)       → AgentMessage (discrete message)
  PostToolUse{...}                        → (ignored — no matching AgentEvent)
  Stop{last_assistant_message, ...}       → AgentResult (ends the turn stream)
"""

import asyncio
from dataclasses import dataclass, field

from app.domain.agent.service import (
    AgentDeliveryFailure,
    AgentEvent,
    AgentMessage,
    AgentResult,
    AgentSessionInfo,
    AgentToolUse,
    AgentUsage,
)
from app.domain.usage.tokens import input_output_tokens


def _hook_event_name(hook: dict) -> str:
    """The hook's event name. Claude Code sends `hook_event_name`; accept the
    camelCase alias too so a payload-shape change doesn't silently break us."""
    return str(hook.get("hook_event_name") or hook.get("hookEventName") or "")


def _usage_from_hook(hook: dict) -> AgentUsage:
    """Best-effort token accounting from a Stop payload. Interactive hooks don't
    reliably carry usage, so this is zero unless a `usage` dict is present — the
    turn is never blocked on missing usage (design note: 拿不到就置 0)."""
    usage = hook.get("usage")
    if not isinstance(usage, dict):
        return AgentUsage()
    # Anthropic-shaped payload: cache buckets fold into input (usage.tokens).
    input_tokens, output_tokens = input_output_tokens(usage)
    return AgentUsage(
        model=str(usage.get("model") or ""),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=float(usage.get("cost_usd") or 0.0),
    )


def translate_hook(hook: dict) -> AgentEvent | AgentDeliveryFailure | None:
    """One hook payload → one AgentEvent, or None when the hook has no
    platform-visible counterpart (e.g. PostToolUse). A returned AgentResult
    signals the end of the turn (the Stop hook)."""
    event = _hook_event_name(hook)

    if event == "SessionStart":
        sid = hook.get("session_id")
        return AgentSessionInfo(session_id=str(sid)) if sid else None

    if event == "PreToolUse":
        tool_input = hook.get("tool_input")
        eid = hook.get("_eid")
        return AgentToolUse(
            name=str(hook.get("tool_name") or ""),
            input=tool_input if isinstance(tool_input, dict) else {},
            eid=eid if isinstance(eid, str) else None,
        )

    if event == "MessageDisplay":
        # A discrete 芝士 message (Slack-style), not a token delta: one
        # MessageDisplay = one chat message block (spike mapping).
        text = hook.get("delta")
        if isinstance(text, str) and text.strip():
            eid = hook.get("_eid")
            return AgentMessage(text=text, eid=eid if isinstance(eid, str) else None)
        return None

    if event == "CheeseSync":
        # A machine that owns its tree reports whether its push landed. Only the
        # failure is surfaced: on success the work is already visible in the
        # branch, and a message per turn saying so would be noise that trains
        # people to skip it.
        #
        # This has to reach the human. A turn that ends with its work still on
        # the machine looks identical to one that succeeded — that is what let a
        # rejected push read as a completed turn until the machine was deleted
        # and the work went with it.
        if str(hook.get("status")) == "failed":
            branch = hook.get("branch") or "the topic branch"
            return AgentMessage(
                text=(
                    f"⚠️ 这轮的改动没能推回 {branch}——它还留在那台机器上，"
                    "采纳和 diff 现在看不到它。请重试本轮；若机器被回收，改动会丢失。"
                )
            )
        return None

    if event == "CheeseDeliveryFailed":
        # Synthetic (device_hub, from the cheeselet's server call): the prompt
        # driver abandoned delivery. Surfaced as a typed event the provider
        # loop intercepts for an immediate re-send (#445) — without this, the
        # give-up lived only in the connector's journal and the room stared at
        # silence until the 300s no-output bound.
        return AgentDeliveryFailure(
            phase=str(hook.get("phase") or ""),
            ticks=int(hook.get("ticks") or 0),
        )

    if event == "CheeseDeliveryRetried":
        # Synthetic (same channel): delivery eventually succeeded but needed
        # noticeably many re-issues — the pane's input path is flaky. Visible
        # so a wobbling machine is seen before it produces a dead turn.
        tries = int(hook.get("ticks") or 0)
        return AgentMessage(
            text=(
                f"⚠️ 提示词经过 {tries} 次重试才送进这台机器的会话——"
                "机器的终端链路在抖，值得看一眼。"
            )
        )

    if event == "Stop":
        sid = hook.get("session_id")
        return AgentResult(
            text=str(hook.get("last_assistant_message") or ""),
            session_id=str(sid) if sid else None,
            usage=_usage_from_hook(hook),
        )

    # PostToolUse and any unmapped event: nothing to surface.
    return None


@dataclass(eq=False)
class HookSink:
    """One screen-lifetime hook inbox."""

    queue: asyncio.Queue[dict] = field(default_factory=asyncio.Queue)


class HookRouter:
    """Process-global router from topic id to a screen-lifetime hook sink.

    The endpoint and provider run on the same asyncio loop, so ``put_nowait`` is
    safe. Re-subscribing is idempotent: a second caller gets the existing sink
    instead of replacing it and starving its consumer.
    """

    def __init__(self) -> None:
        self._sinks: dict[str, HookSink] = {}

    def subscribe(self, topic_id: str) -> HookSink:
        """Return the topic's stable sink, creating it on first live screen."""
        sink = self._sinks.get(topic_id)
        if sink is None:
            sink = HookSink()
            self._sinks[topic_id] = sink
        return sink

    def unsubscribe(self, topic_id: str, sink: HookSink) -> None:
        """Release a screen's sink without evicting a newer replacement."""
        if self._sinks.get(topic_id) is sink:
            self._sinks.pop(topic_id, None)

    def push(self, topic_id: str, hook: dict) -> bool:
        """Enqueue a hook payload for the topic's subscribed screen."""
        sink = self._sinks.get(topic_id)
        if sink is None:
            return False
        sink.queue.put_nowait(hook)
        return True


# Shared singleton: the endpoint and the provider import this same instance.
hook_router = HookRouter()


def usage_from_hook(hook: dict) -> AgentUsage | None:
    """A turn's real token counts, carried back from the machine.

    Deliberately NOT part of ``translate_hook``: usage is not an event in the
    turn's stream, it is a fact about the turn. Returning it from there made the
    type checker object, and the objection was right — the caller records it,
    the UI never shows it.
    """
    # The machine is the only place these numbers exist — Claude Code writes
    # a usage block per assistant message and the transcript dies with the
    # host. Carrying them back is what turns "300 RMB went somewhere" into a
    # per-project, per-turn figure.
    # Cache reads are NOT free and they dominate; AgentUsage has no cache field,
    # so they fold into the input count (app.domain.usage.tokens — the same
    # arithmetic every supply uses).
    input_tokens, output_tokens = input_output_tokens(hook, dialect="hook")
    if input_tokens + output_tokens <= 0:
        return None
    return AgentUsage(
        model=str(hook.get("model") or "unknown"),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )
