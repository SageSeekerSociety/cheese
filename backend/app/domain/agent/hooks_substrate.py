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
import uuid
from collections.abc import AsyncIterator

from app.core.sandbox_auth import mint_scoped_token
from app.domain.agent.hook_events import HookRouter, hook_router, translate_hook
from app.domain.agent.service import AgentEvent, AgentResult

# The interactive session's hook token outlives a single turn (the screen / tmux
# session is reused across turns), so it needs a lifetime measured in the
# session's life, not a turn's. Topic-scoped, so a stale one still can't reach
# another topic. Shared by both backends.
SESSION_TOKEN_TTL_S = 30 * 24 * 3600


def hooks_settings(extra_stop: list[str] | None = None) -> dict:
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
    # A remote machine also has to hand its work back at turn end; the local
    # container edits the real worktree and has nothing to send.
    stop_hooks = [cmd] + [
        {"type": "command", "command": name} for name in (extra_stop or [])
    ]
    return {
        "skipDangerousModePermissionPrompt": True,
        "hooks": {
            "SessionStart": plain,
            "PreToolUse": tool_matched,
            "PostToolUse": tool_matched,
            "MessageDisplay": plain,
            "Stop": [{"hooks": stop_hooks}],
        },
    }


# The forwarder: reads a Claude Code hook's JSON on stdin, durably spools it (when
# CHEESE_HOOK_SPOOL is set — the local/tmux backend only) so the event survives a
# backend outage, then best-effort POSTs it with the screen's token + a stable
# per-event id (X-Cheese-Event-Id, used to dedup the spool backfill against the live
# delivery). Exit 0 + empty stdout = "no decision" → the tool proceeds. The device
# backend writes this via its launcher; the local (tmux) image bakes the same script
# (kept identical so sensing can't drift); the spool block no-ops without the env.
# NOTE: the device launcher embeds this in a <<'SH' heredoc — never add a line
# consisting of just `SH` here or the heredoc would silently truncate.
CHEESE_HOOK_SCRIPT = """#!/bin/sh
body="$(cat)"
eid="$(cat /proc/sys/kernel/random/uuid 2>/dev/null || echo "$$-$(date +%s%N)")"
if [ -n "$CHEESE_HOOK_SPOOL" ]; then
  mkdir -p "$CHEESE_HOOK_SPOOL" 2>/dev/null || true
  _tmp="$CHEESE_HOOK_SPOOL/.tmp.$eid"
  _dst="$CHEESE_HOOK_SPOOL/$(date +%s%N 2>/dev/null).$eid"
  if printf '%s' "$body" > "$_tmp" 2>/dev/null; then
    mv "$_tmp" "$_dst" 2>/dev/null || rm -f "$_tmp" 2>/dev/null
  fi
fi
# On the device a background drainer is the sole sender (CHEESE_HOOK_SPOOL_ONLY set);
# locally we curl inline for low latency (the backend reconciles the spool for gaps).
if [ -z "$CHEESE_HOOK_SPOOL_ONLY" ] && [ -n "$CHEESE_HOOK_URL" ]; then
  printf '%s' "$body" | curl -s -m 10 -X POST \\
    -H 'Content-Type: application/json' \\
    -H "X-Cheese-Token: $CHEESE_TOKEN" \\
    -H "X-Cheese-Event-Id: $eid" \\
    --data-binary @- "$CHEESE_HOOK_URL" >/dev/null 2>&1 || true
fi
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


class ScreenSetupError(Exception):
    """A backend couldn't bring the screen to a prompt-ready state (no Docker /
    no online device / not ready in time). Its message becomes the turn's error
    result — the ONE place setup failures turn into an ``AgentResult``."""


class HooksTurnProvider[ScreenT]:
    """Base for the hooks-driven backends (fusion-design §8.6, increment 2).

    Owns the transport-INDEPENDENT turn — ONE flow for local and remote so they
    can't drift: check the topic, mint the session-scoped token, register the
    topic's hook queue BEFORE any prompt (so no hook is missed), bring up a screen
    + inject the prompt (subclass transport), then drain hooks → ``AgentEvent``
    until Stop, and always release the queue.

    A subclass ("本地/远程只差 transport/入册") implements only the transport seam:
    ``_precheck`` (cheap fail-fast BEFORE the queue is claimed), ``_ensure_ready``
    (→ a screen ctx of type ``ScreenT``) and ``_send_prompt``, plus the class-level
    ``name`` / ``_needs_topic_message`` / ``_timeout_message``. All three raise
    ``ScreenSetupError`` to surface a clean error result. This is the strategy
    behind ``TmuxHooksProvider`` (local docker/tmux) and ``DeviceProvider``
    (remote link.Msg)."""

    name: str = "hooks"
    _needs_topic_message = "本轮需要话题上下文"
    _timeout_message = "轮次超时"

    def __init__(
        self, *, router: HookRouter | None = None, turn_timeout_s: float = 900.0
    ) -> None:
        self._router = router or hook_router
        self._turn_timeout_s = turn_timeout_s

    def available(self) -> bool:
        return True

    async def _precheck(self, project_id: uuid.UUID, topic_id: uuid.UUID) -> object:
        """Cheap fail-fast checks that run BEFORE the token is minted and the hook
        queue is claimed (preserves the pre-refactor ordering: a turn that can't
        run at all never touches the router — review finding). Raise
        ``ScreenSetupError`` to end the turn with a clean error result. The return
        value is handed to ``_ensure_ready`` as ``precheck`` so a subclass doesn't
        resolve twice (e.g. the device backend resolves its pinned device here)."""
        return None

    async def _ensure_ready(
        self,
        *,
        project_id: uuid.UUID,
        topic_id: uuid.UUID,
        token: str,
        model: str | None,
        env: dict[str, str] | None,
        memory_scope: str | None,
        owner: str | None,
        turn_id: uuid.UUID | None,
        resume_session_id: str | None,
        precheck: object,
    ) -> ScreenT:
        """Bring the topic's screen to a prompt-ready state; raise
        ``ScreenSetupError`` if it can't be. Transport-specific (subclass)."""
        raise NotImplementedError

    async def _send_prompt(self, screen: ScreenT, prompt: str) -> None:
        """Deliver the turn's prompt to the ready screen. Transport-specific."""
        raise NotImplementedError

    def checkpoint(self, project_id: uuid.UUID, topic_id: uuid.UUID) -> None:
        """Snapshot the turn's edits into version history. Default no-op (the
        device owns its own tree); the local backend overrides to git-snapshot."""
        return

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
        if topic_id is None:
            yield AgentResult(
                text=self._needs_topic_message,
                session_id=resume_session_id,
                is_error=True,
            )
            return

        # Fail-fast BEFORE claiming the topic's queue (no Docker / no online
        # device): a turn that can't run must never evict a live queue or widen
        # the stale-hook window (review finding — matches pre-refactor ordering).
        try:
            precheck = await self._precheck(project_id, topic_id)
        except ScreenSetupError as exc:
            yield AgentResult(
                text=str(exc), session_id=resume_session_id, is_error=True
            )
            return

        topic_key = str(topic_id)
        token = mint_scoped_token(
            project_id=str(project_id),
            topic_id=topic_key,
            ttl_s=SESSION_TOKEN_TTL_S,
        )
        # Register the queue BEFORE bringing up the screen / sending the prompt so
        # no hook is missed.
        queue = self._router.register(topic_key)
        try:
            try:
                screen = await self._ensure_ready(
                    project_id=project_id,
                    topic_id=topic_id,
                    token=token,
                    model=model,
                    env=env,
                    memory_scope=memory_scope,
                    owner=owner,
                    turn_id=turn_id,
                    resume_session_id=resume_session_id,
                    precheck=precheck,
                )
                await self._send_prompt(screen, prompt)
            except ScreenSetupError as exc:
                yield AgentResult(
                    text=str(exc), session_id=resume_session_id, is_error=True
                )
                return
            async for event in run_hooks_turn(
                queue=queue,
                turn_timeout_s=self._turn_timeout_s,
                resume_session_id=resume_session_id,
                timeout_message=self._timeout_message,
            ):
                yield event
        finally:
            self._router.unregister(topic_key, queue)
