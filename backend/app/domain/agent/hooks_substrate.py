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
import logging
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass

from app.core.sandbox_auth import mint_scoped_token
from app.domain.agent import event_spool
from app.domain.agent.hook_events import HookRouter, hook_router, translate_hook
from app.domain.agent.platform_failures import (
    PROMPT_UNDELIVERED_MESSAGE,
    TURN_TIMEOUT_MESSAGE,
)
from app.domain.agent.service import DISALLOWED_TOOLS, AgentEvent, AgentResult
from app.domain.workspace import service as ws

logger = logging.getLogger(__name__)

# The interactive session's hook token outlives a single turn (the screen / tmux
# session is reused across turns), so it needs a lifetime measured in the
# session's life, not a turn's. Topic-scoped, so a stale one still can't reach
# another topic. Shared by both backends.
SESSION_TOKEN_TTL_S = 30 * 24 * 3600


def _park_stale_hook(
    project_id: uuid.UUID, topic_id: uuid.UUID, eid: str, payload: dict
) -> None:
    """Durably park a hook this turn refuses to trust (see ``HookRouter.drain``)
    into the same spool a later reconcile drains — best-effort, mirrors the
    /sandbox/hooks endpoint's own park-on-no-listener path."""
    try:
        event_spool.append(ws.spool_dir(project_id, topic_id), eid, payload)
    except Exception:  # noqa: BLE001 — parking is best-effort
        logger.warning("stale hook park failed for topic %s", topic_id, exc_info=True)


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
        # Tools with no way out of this platform (AskUserQuestion — see
        # service.DISALLOWED_TOOLS). Also passed as --disallowedTools on the
        # launch line; a deny rule that only lives in one of the two is a deny
        # rule that a future launcher tweak can silently drop.
        "permissions": {"deny": list(DISALLOWED_TOOLS)},
        "hooks": {
            "SessionStart": plain,
            # The delivery receipt. We inject a prompt by typing it into the
            # terminal, and typing has no return value: tmux confirms the bytes
            # reached the pane and nothing confirms a prompt box read them. This
            # hook fires for pasted input exactly as for a human's keystrokes,
            # so its arrival is the proof that the message became a user turn.
            "UserPromptSubmit": plain,
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
  # Shared bind mount: node (sandbox uid 1000) writes while cheese (backend uid
  # 1001) reconciles, parks, and removes events. Keep the directory shared even
  # if it had to be recreated after session setup.
  chmod 0777 "$CHEESE_HOOK_SPOOL" 2>/dev/null || true
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


# How long a turn waits for ANY sign the prompt was received before calling it
# undelivered. Generous enough for a busy container to schedule the hook,
# far short of the turn ceiling — the point is that "nothing arrived" is
# reported in seconds instead of being indistinguishable from "still working"
# for fifteen minutes (dev, 2026-08-08).
DELIVERY_TIMEOUT_S = 25.0
# The wording itself lives in `platform_failures` — that module classifies this
# failure by matching the platform's own marker, so keeping one copy is what
# stops the sentence and its classification from drifting apart (the drift is
# what made this render as 「AI 服务返回错误」).
UNDELIVERED_MESSAGE = PROMPT_UNDELIVERED_MESSAGE


@dataclass
class ActivityTracker:
    """Per-turn shared clock for the two-layer idle-suspect / hard-ceiling check
    (turn 活跃度检测, 2026-08-09). Written from up to two places: ``run_hooks_turn``
    itself on every hook arrival, and — local tmux backend only — a background
    capture-pane poller (``TmuxHooksProvider._start_activity_monitor``), so a
    long tool call with no interim hook but a busy pane still counts as active,
    not just hook arrivals. ``suspect_since`` is surfaced by ``cheese status``."""

    last_at: float
    suspect_since: float | None = None

    def touch(self, at: float) -> None:
        self.last_at = at
        self.suspect_since = None


# How often a suspected-wedged turn re-checks liveness while it stays idle (a
# single ``confirm_alive`` at the 5-minute mark isn't enough — the screen could
# die at minute 6 and go unnoticed until the 3-hour hard ceiling otherwise).
# Cheap by design (e.g. a tmux capture-pane / list-panes call), so a short
# cadence costs nothing.
CONFIRM_POLL_S = 15.0


async def run_hooks_turn(
    *,
    queue: "asyncio.Queue[dict]",
    idle_suspect_s: float,
    hard_ceiling_s: float,
    resume_session_id: str | None,
    timeout_message: str,
    delivery_timeout_s: float = DELIVERY_TIMEOUT_S,
    delivery_message: str = UNDELIVERED_MESSAGE,
    tracker: ActivityTracker | None = None,
    confirm_alive: Callable[[], Awaitable[bool]] | None = None,
    confirm_poll_s: float = CONFIRM_POLL_S,
) -> AsyncIterator[AgentEvent]:
    """Drain the topic's hook queue, translating each hook to an ``AgentEvent``,
    until the ``Stop`` hook (→ ``AgentResult``) ends the turn. Transport-independent:
    both the tmux and device backends run this identical loop after their
    transport-specific ensure-screen + send-prompt.

    Two layers replace the old single static deadline (turn 活跃度检测, review:
    a static ``deadline - now()`` can't tell "still working" from "wedged"):

    - ``idle_suspect_s``: below this much idle time (no hook AND, if ``tracker``
      is fed by a backend-specific side channel, no other activity signal) a
      turn is normal. Past it the turn is only SUSPECTED wedged — ``confirm_alive``
      (if given) is polled every ``confirm_poll_s`` until it says the screen is
      actually dead, or activity resumes and clears the suspicion.
    - ``hard_ceiling_s``: an unconditional backstop regardless of activity, so a
      pathologically "active" turn (a tool retrying forever, a real infinite
      loop that keeps printing) still can't run forever.

    With no ``tracker``/``confirm_alive`` given (the device backend today) and
    ``idle_suspect_s == hard_ceiling_s``, this reduces to exactly the old
    single-deadline behaviour.

    The CALLER owns the queue lifecycle — it must ``router.register`` BEFORE
    ensuring the screen / sending the prompt (so no hook is missed) and
    ``unregister`` in a ``finally``; this loop only reads the queue."""
    now = asyncio.get_event_loop().time
    start = now()
    hard_deadline = start + hard_ceiling_s
    tracker = tracker if tracker is not None else ActivityTracker(last_at=start)
    # Until something comes back, we have no evidence the prompt was received at
    # all: it is typed into a terminal, and typing has no return value. So the
    # first wait is short. Any hook clears it — `UserPromptSubmit` is the direct
    # receipt, and any other activity proves delivery just as well.
    delivered = False
    delivery_deadline = start + delivery_timeout_s
    while True:
        t = now()
        if t >= hard_deadline:
            yield AgentResult(
                text=timeout_message, session_id=resume_session_id, is_error=True
            )
            return
        if delivered:
            idle_for = t - tracker.last_at
            if idle_for >= idle_suspect_s:
                wait_for = min(confirm_poll_s, hard_deadline - t)
            else:
                wait_for = min(idle_suspect_s - idle_for, hard_deadline - t)
        else:
            wait_for = min(hard_deadline - t, delivery_deadline - t)
        try:
            hook = await asyncio.wait_for(queue.get(), timeout=max(wait_for, 0.01))
        except TimeoutError:
            if not delivered:
                if now() >= delivery_deadline:
                    yield AgentResult(
                        text=delivery_message,
                        session_id=resume_session_id,
                        is_error=True,
                    )
                    return
                continue
            idle_for = now() - tracker.last_at
            if idle_for >= idle_suspect_s:
                if tracker.suspect_since is None:
                    tracker.suspect_since = now()
                alive = await confirm_alive() if confirm_alive is not None else True
                if not alive:
                    yield AgentResult(
                        text=timeout_message,
                        session_id=resume_session_id,
                        is_error=True,
                    )
                    return
            continue
        delivered = True
        tracker.touch(now())
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
    # A subclass may prefix its transport ("tmux 轮次超时"), but it must keep
    # `TURN_TIMEOUT_MARKER` in the string — that marker is how the failure gets
    # classified as a turn timeout instead of being blamed on the AI service.
    _timeout_message = TURN_TIMEOUT_MESSAGE

    def __init__(
        self,
        *,
        router: HookRouter | None = None,
        idle_suspect_s: float = 900.0,
        hard_ceiling_s: float = 900.0,
    ) -> None:
        self._router = router or hook_router
        # Equal by default → run_hooks_turn's idle-suspect check and hard-ceiling
        # check land on the same instant, i.e. the old single-deadline behaviour
        # (the device backend keeps this; see DeviceProvider).
        self._idle_suspect_s = idle_suspect_s
        self._hard_ceiling_s = hard_ceiling_s

    @property
    def hard_ceiling_s(self) -> float:
        """This provider's effective absolute turn ceiling — read by TurnRunner
        to reschedule its own transport-independent outer wall-clock wrap
        (runtime.py) so a backend with a longer ceiling than
        ``settings.agent_turn_timeout_s`` (today: the tmux backend) isn't killed
        early by that unrelated outer guard."""
        return self._hard_ceiling_s

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
        system_prompt: str,
        precheck: object,
    ) -> ScreenT:
        """Bring the topic's screen to a prompt-ready state; raise
        ``ScreenSetupError`` if it can't be. Transport-specific (subclass).

        ``system_prompt`` is the platform's assembled system prompt and MUST be
        delivered to the `claude` this screen hosts (``--append-system-prompt``
        at launch). It is a launch-time input, not a per-prompt one: a screen
        that is merely reused keeps the prompt it was started with."""
        raise NotImplementedError

    async def _send_prompt(self, screen: ScreenT, prompt: str) -> None:
        """Deliver the turn's prompt to the ready screen. Transport-specific."""
        raise NotImplementedError

    async def _start_activity_monitor(
        self, screen: ScreenT, tracker: ActivityTracker
    ) -> asyncio.Task | None:
        """Optional background activity signal alongside hook arrivals (e.g. the
        tmux backend's capture-pane polling — a long tool call between hooks
        must still count as "alive"). Return a task that keeps ``tracker``
        touched; ``run_turn`` cancels it when the turn ends.

        Default: no extra signal, activity is judged from hook arrivals alone —
        correct for the device backend today (TODO: an equivalent remote
        activity probe, e.g. ``device_hub`` screen bytes, is future work; see
        ``DeviceProvider``)."""
        return None

    async def _confirm_alive(self, screen: ScreenT) -> bool:
        """Called (repeatedly, while idle persists) once the idle-suspect
        threshold is crossed, to confirm the screen isn't actually dead before
        treating the idle window as fatal. Default: assume alive — no cheap
        probe exists at this level. ``TmuxHooksProvider`` overrides with
        ``pane_dead()``."""
        return True

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
                    system_prompt=system_prompt,
                    precheck=precheck,
                )
                # Nothing has been sent to `claude` yet, so anything already
                # sitting in the queue at this point is a straggler from a
                # PREVIOUS, abandoned turn (its own late Stop included) — never
                # this turn's own event. Park it exactly like a hook that
                # arrived with no turn listening at all (never dropped, never
                # mistaken for this turn's result — review finding: a stale
                # Stop must never end the wrong turn).
                for stale in self._router.drain(topic_key):
                    eid = stale.get("_eid")
                    if isinstance(eid, str):
                        _park_stale_hook(project_id, topic_id, eid, stale)
                await self._send_prompt(screen, prompt)
            except ScreenSetupError as exc:
                yield AgentResult(
                    text=str(exc), session_id=resume_session_id, is_error=True
                )
                return
            tracker = ActivityTracker(last_at=asyncio.get_event_loop().time())
            monitor_task = await self._start_activity_monitor(screen, tracker)
            try:
                async for event in run_hooks_turn(
                    queue=queue,
                    idle_suspect_s=self._idle_suspect_s,
                    hard_ceiling_s=self._hard_ceiling_s,
                    resume_session_id=resume_session_id,
                    timeout_message=self._timeout_message,
                    tracker=tracker,
                    confirm_alive=lambda: self._confirm_alive(screen),
                ):
                    yield event
            finally:
                if monitor_task is not None:
                    monitor_task.cancel()
                    try:
                        await monitor_task
                    except asyncio.CancelledError:
                        pass
        finally:
            self._router.unregister(topic_key, queue)
