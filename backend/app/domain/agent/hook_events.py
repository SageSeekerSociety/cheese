"""Claude Code hooks → AgentEvent (tmux backend).

The interactive `claude` running in a tmux session emits structured events via
Claude Code hooks. (Command hooks, not HTTP: Claude Code blocks HTTP hooks to
non-loopback targets, so a baked `cheese-hook` script reads the hook JSON on
stdin and POSTs it to /sandbox/hooks/{topic}.) This module is the pure,
docker-free core of the tmux backend:

- ``translate_hook`` maps ONE hook payload to an AgentEvent (spec §9.1: the
  platform observes 芝士 through structured events, never by parsing prose).
- ``HookRouter`` fans hook POSTs (from the /sandbox/hooks endpoint) to the
  asyncio.Queue of the turn currently running for that topic. Turns are
  serialized per topic (topic lock), so at most one queue is active per topic.

Event mapping (verified in the spike, docs/tmux-backend-spike.md):
  SessionStart{session_id}                → AgentSessionInfo
  PreToolUse{tool_name, tool_input}       → AgentToolUse
  MessageDisplay{delta} (non-empty)       → AgentMessage (discrete message)
  PostToolUse{...}                        → (ignored — no matching AgentEvent)
  Stop{last_assistant_message, ...}       → AgentResult (ends the turn stream)
"""

import asyncio

from app.domain.agent.service import (
    AgentEvent,
    AgentMessage,
    AgentResult,
    AgentSessionInfo,
    AgentToolUse,
    AgentUsage,
)


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
    return AgentUsage(
        model=str(usage.get("model") or ""),
        input_tokens=int(usage.get("input_tokens") or 0),
        output_tokens=int(usage.get("output_tokens") or 0),
        cost_usd=float(usage.get("cost_usd") or 0.0),
    )


def translate_hook(hook: dict) -> AgentEvent | None:
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

    if event == "Stop":
        sid = hook.get("session_id")
        return AgentResult(
            text=str(hook.get("last_assistant_message") or ""),
            session_id=str(sid) if sid else None,
            usage=_usage_from_hook(hook),
        )

    # PostToolUse and any unmapped event: nothing to surface.
    return None


class HookRouter:
    """Process-global router from topic id → the active turn's event queue.

    The /sandbox/hooks endpoint calls ``push``; TmuxHooksProvider.run_turn holds
    the matching queue via ``register`` for the duration of the turn. Both run on
    the same asyncio loop (uvicorn worker), so put_nowait is safe and lock-free.
    Turns are serialized per topic, so one queue per topic is sufficient."""

    def __init__(self) -> None:
        self._queues: dict[str, asyncio.Queue[dict]] = {}

    def register(self, topic_id: str) -> asyncio.Queue[dict]:
        """Claim the topic's slot for this turn and return its fresh queue. A new
        queue REPLACES any stale one (a previous turn that failed to clean up)."""
        queue: asyncio.Queue[dict] = asyncio.Queue()
        self._queues[topic_id] = queue
        return queue

    def unregister(self, topic_id: str, queue: asyncio.Queue[dict]) -> None:
        """Release the topic's slot — but only if it still holds OUR queue, so a
        late cleanup never evicts the next turn's already-registered queue."""
        if self._queues.get(topic_id) is queue:
            self._queues.pop(topic_id, None)

    def push(self, topic_id: str, hook: dict) -> bool:
        """Enqueue a hook payload for the topic's active turn. Returns False when
        no turn is listening (hook arrived outside a run_turn window) so the
        endpoint can report it instead of silently dropping."""
        queue = self._queues.get(topic_id)
        if queue is None:
            return False
        queue.put_nowait(hook)
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
    total = sum(
        int(hook.get(k) or 0) for k in ("input", "output", "cache_read", "cache_write")
    )
    if total <= 0:
        return None
    return AgentUsage(
        model=str(hook.get("model") or "unknown"),
        # Cache reads are NOT free and they dominate: one document-writing
        # task read 2.9M cached tokens against 141k of fresh input — 20x, and
        # half its cost. AgentUsage has no cache field, so they are folded
        # into the input count; leaving them out would under-report a turn by
        # more than it reports.
        input_tokens=(
            int(hook.get("input") or 0)
            + int(hook.get("cache_read") or 0)
            + int(hook.get("cache_write") or 0)
        ),
        output_tokens=int(hook.get("output") or 0),
    )
