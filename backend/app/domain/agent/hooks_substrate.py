"""Shared substrate for the hooks-driven `claude` backends (fusion-design §8.6).

Both the LOCAL backend (``TmuxHooksProvider`` — a per-topic tmux session in a
platform container) and the REMOTE backend (``DeviceProvider`` — a screen on a
user's enrolled machine over the frozen ``link.Msg`` channel) drive an
interactive ``claude`` and sense it through the SAME Claude Code hooks. Enrollment
and transport aside, a turn is IDENTICAL: register the topic's hook queue, ensure
a screen + inject the prompt (the only transport-specific step), then drain hooks
→ ``AgentEvent`` until the ``Stop`` hook ends it.

Per the fusion 统一底座 decision, the transport-INDEPENDENT parts live here so the
two backends are one substrate that can't drift — "本地/远程只差入册，不两套":

- ``hooks_settings()`` — the ``~/.claude/settings.json`` wiring Claude Code COMMAND
  hooks to the ``cheese-hook`` forwarder (HTTP hooks are blocked to non-loopback).
- ``CHEESE_HOOK_SCRIPT`` — the forwarder: POST each hook's JSON to the backend.
- ``run_hooks_turn(...)`` — the shared drain loop (drain → translate → Stop ends).
- ``SESSION_TOKEN_TTL_S`` — the shared session-length scoped-token TTL.

All pure / transport-free, so it is unit-testable without Docker or a device.
"""

import asyncio
from collections.abc import AsyncIterator

from app.domain.agent.hook_events import translate_hook
from app.domain.agent.service import AgentEvent, AgentResult

# The interactive session's hook token outlives a single turn (the screen / tmux
# session is reused across turns), so it needs a lifetime measured in the
# session's life, not a turn's. Topic-scoped, so a stale one still can't reach
# another topic. Shared by both backends.
SESSION_TOKEN_TTL_S = 30 * 24 * 3600


def hooks_settings() -> dict:
    """``~/.claude/settings.json`` for a hooks-driven session: pre-accept the
    bypass disclaimer AND forward every structured event to our hook endpoint via
    a COMMAND hook (``cheese-hook``).

    Command (not the built-in ``"type":"http"``) hooks: Claude Code 2.1.x BLOCKS
    HTTP hooks whose host resolves to a non-loopback / private IP, and only
    127.0.0.1/::1 are allowed — which a container / device can't use to reach the
    backend. The ``cheese-hook`` forwarder reads the hook JSON on stdin and POSTs
    it to ``CHEESE_HOOK_URL`` with the ``CHEESE_TOKEN`` header, sidestepping that.

    Shared by the local (tmux) and remote (device) backends so their perception
    wiring is one thing — change it here, both backends move together."""
    cmd = {"type": "command", "command": "cheese-hook"}
    tool_matched = [{"matcher": "*", "hooks": [cmd]}]
    plain = [{"hooks": [cmd]}]
    return {
        "skipDangerousModePermissionPrompt": True,
        "hooks": {
            "SessionStart": plain,
            "PreToolUse": tool_matched,
            "PostToolUse": tool_matched,
            "MessageDisplay": plain,
            "Stop": plain,
        },
    }


# The forwarder: reads a Claude Code hook's JSON on stdin and POSTs it to the
# backend with the screen's token. Exit 0 + empty stdout = "no decision" → the
# tool proceeds. The device backend writes this via its launcher; the local
# (tmux) image bakes the same script (kept identical so sensing can't drift).
CHEESE_HOOK_SCRIPT = """#!/bin/sh
[ -n "$CHEESE_HOOK_URL" ] || exit 0
curl -s -m 10 -X POST \\
  -H 'Content-Type: application/json' \\
  -H "X-Cheese-Token: $CHEESE_TOKEN" \\
  --data-binary @- "$CHEESE_HOOK_URL" >/dev/null 2>&1 || true
exit 0
"""


async def run_hooks_turn(
    *,
    queue: "asyncio.Queue[dict]",
    turn_timeout_s: float,
    resume_session_id: str | None,
    timeout_message: str,
) -> AsyncIterator[AgentEvent]:
    """Drain the topic's hook queue, translating each hook to an ``AgentEvent``,
    until the ``Stop`` hook (→ ``AgentResult``) ends the turn or the deadline
    passes. Transport-independent: both the tmux and device backends run this
    identical loop after their transport-specific ensure-screen + send-prompt.

    The CALLER owns the queue lifecycle — it must ``router.register`` BEFORE
    ensuring the screen / sending the prompt (so no hook is missed) and
    ``unregister`` in a ``finally``; this loop only reads the queue."""
    deadline = asyncio.get_event_loop().time() + turn_timeout_s
    while True:
        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            yield AgentResult(
                text=timeout_message, session_id=resume_session_id, is_error=True
            )
            return
        try:
            hook = await asyncio.wait_for(queue.get(), timeout=remaining)
        except TimeoutError:
            yield AgentResult(
                text=timeout_message, session_id=resume_session_id, is_error=True
            )
            return
        event = translate_hook(hook)
        if event is None:
            continue
        yield event
        if isinstance(event, AgentResult):
            return  # Stop hook → turn done
