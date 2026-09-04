"""Claude Code, and the one seam a transport plugs into (fusion-design §8.6).

``ClaudeCodeRuntime`` drives an interactive ``claude`` and senses it through
Claude Code hooks. ``Channel`` is the transport it drives over: a per-topic tmux
session in a platform container, a screen on someone's enrolled machine across
the frozen ``link.Msg`` link, a leased Cloud machine reached the same way.

The split is the whole file. Everything the runtime does — subscribe before
screen setup, spool the hooks, watch for a session that has gone quiet, report
receipts, hold the turn's attribution — is a cost of driving a TUI written for a
person, and none of it changes with the transport. It used to be a base class,
so each transport carried its own copy and a second harness would have needed
one copy per transport. A channel now answers two questions (bring a screen up,
put text into it) and never hears the word "hook" — nor the word "claude": what
to run arrives as a ``LaunchPlan`` it hands its own coordinates to.

Also here, all transport-free and unit-testable without Docker or a device:

- ``CHEESE_HOOK_SCRIPT`` — the forwarder: POST each hook's JSON to the backend.
- ``monitor_session_activity(...)`` — shared idle, liveness, and ceiling policy.
- ``SESSION_TOKEN_TTL_S`` — the shared session-length scoped-token TTL.
"""

import asyncio
import logging
import uuid
import weakref
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.core.sandbox_auth import mint_scoped_token
from app.domain.agent.harness import (
    CLAUDE_CODE,
    ActivityConsumer,
    Backlog,
    EventConsumer,
    HarnessEvent,
    Opening,
    ReceiptConsumer,
    SessionRef,
    UnreadProbe,
)
from app.domain.agent.harness.claude_code import event_spool
from app.domain.agent.harness.claude_code.hook_events import (
    RECORDED_AT_KEY,
    HookRouter,
    HookSink,
    MessageAssembler,
    _hook_event_name,
    hook_router,
    translate_hook,
)
from app.domain.agent.harness.claude_code.session_launch import ClaudeLaunch
from app.domain.agent.harness.launch import LaunchPlan
from app.domain.agent.platform_failures import (
    PROMPT_UNDELIVERED_CODE,
    PROMPT_UNDELIVERED_MESSAGE,
    TURN_TIMEOUT_CODE,
    TURN_TIMEOUT_MESSAGE,
)
from app.domain.agent.service import (
    AgentEvent,
    AgentMessage,
    AgentResult,
    AgentToolResult,
    AgentToolUse,
)
from app.domain.workspace import service as ws

logger = logging.getLogger(__name__)

# Screen teardown starts down in a channel while subscriptions stay owned up
# here. Weak references avoid keeping rebuilt pools and test runtimes alive.
_RUNTIMES: weakref.WeakSet[object] = weakref.WeakSet()

# The hook token lives with the interactive screen. Topic scope prevents a stale
# token from reaching another topic. Both transports use the same lifetime.
SESSION_TOKEN_TTL_S = 30 * 24 * 3600


# The forwarder: reads a Claude Code hook's JSON on stdin, durably spools it (when
# CHEESE_HOOK_SPOOL is set — the local/tmux backend only) so the event survives a
# backend outage, then best-effort POSTs it with the screen's token + a stable
# per-event id (X-Cheese-Event-Id, used to dedup the spool backfill against the live
# delivery). Exit 0 + empty stdout = "no decision" → the tool proceeds. The device
# backend writes this via its launcher; the local (tmux) image bakes the same script
# (kept identical so sensing can't drift); the spool block no-ops without the env.
#
# The spooled name is `<seq>.<eid>`, and `seq` is claimed the way event_spool.py
# claims it — an O_EXCL create under `set -C`, seeded from a `.seq` hint. That
# module's docstring says why the clock was not good enough; the short of it is
# that a `date` without `%N` (any BSD userland, i.e. a device on macOS) sorts a
# whole second's events at random, and a `date` that fails at all produces a
# name starting with `.`, which every reader skips forever.
# NOTE: the device launcher embeds this in a <<'SH' heredoc — never add a line
# consisting of just `SH` here or the heredoc would silently truncate.
CHEESE_HOOK_SCRIPT = """#!/bin/sh
body="$(cat)"
eid="$(cat /proc/sys/kernel/random/uuid 2>/dev/null || echo "$$-$(date +%s%N)")"
if [ -n "$CHEESE_HOOK_SPOOL" ]; then
  mkdir -p "$CHEESE_HOOK_SPOOL" 2>/dev/null || true
  # Shared bind mount: node (sandbox uid 1000) writes while cheese (backend uid
  # 1001) reads, parks, and prunes events. Keep the directory shared even if it
  # had to be recreated after session setup.
  chmod 0777 "$CHEESE_HOOK_SPOOL" 2>/dev/null || true
  _n="$(cat "$CHEESE_HOOK_SPOOL/.seq" 2>/dev/null)"
  case "$_n" in
    ''|*[!0-9]*)
      # No usable hint. The glob expands in ascending order and every name is
      # the same width, so the last one that parses is the highest.
      _n=0
      for _f in "$CHEESE_HOOK_SPOOL"/[0-9]*; do
        [ -e "$_f" ] || continue
        _b="${_f##*/}"
        _b="${_b%%.*}"
        case "$_b" in ''|*[!0-9]*) continue;; esac
        _n="$_b"
      done
      # $(( )) reads a zero-padded number as octal; strip the pad first.
      while :; do case "$_n" in 0?*) _n="${_n#0}";; *) break;; esac; done
      ;;
  esac
  while :; do
    _n=$((_n + 1))
    _key="$(printf '%019d' "$_n")"
    # noclobber makes this O_EXCL: one writer owns the number, the rest retry.
    if (set -C; : > "$CHEESE_HOOK_SPOOL/.n$_key") 2>/dev/null; then break; fi
  done
  printf '%s' "$_n" > "$CHEESE_HOOK_SPOOL/.seqt.$$" 2>/dev/null &&
    mv "$CHEESE_HOOK_SPOOL/.seqt.$$" "$CHEESE_HOOK_SPOOL/.seq" 2>/dev/null
  rm -f "$CHEESE_HOOK_SPOOL/.seqt.$$" 2>/dev/null
  _tmp="$CHEESE_HOOK_SPOOL/.tmp.$eid"
  if printf '%s' "$body" > "$_tmp" 2>/dev/null; then
    mv "$_tmp" "$CHEESE_HOOK_SPOOL/$_key.$eid" 2>/dev/null || rm -f "$_tmp" 2>/dev/null
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
# The sentence itself lives in platform_failures, next to the classifier that
# recognises it — a copy here would drift and the failure would silently go back
# to rendering as 「AI 服务返回错误」.
UNDELIVERED_MESSAGE = PROMPT_UNDELIVERED_MESSAGE


# --- the log this harness writes, read from a cursor -------------------------
#
# Claude Code has no event API. What it has is hooks, and every one of them is
# appended to a durable spool before anything downstream sees it — which makes
# that spool this harness's event log, not its backup. `read` is nothing more
# than these: entries after a cursor, the cursor, and a way to move it. Reading
# never consumes, so two readers at different cursors both see the whole tail;
# retention is the only thing that deletes.
#
# Free functions, not methods on a live runtime, because the log is addressed
# by SESSION and outlives every transport that ever wrote to it: the crash
# recovery that reads it runs before any screen has been found, and the settle
# that drains an orphan's tail has no runtime in hand. The methods on
# `ClaudeCodeRuntime` are the same four, so the contract is satisfied by an
# instance too.


def _spool_of(session: SessionRef) -> Path:
    return ws.spool_dir(session.project_id, session.topic_id)


def read_log(session: SessionRef, *, since: str | None = None) -> list[HarnessEvent]:
    """This session's events after ``since``, oldest first."""
    return [
        HarnessEvent(
            key=path.name, eid=eid, record=payload, age_s=event_spool.age_s(path)
        )
        for path, eid, payload in event_spool.spool_entries(
            _spool_of(session), after=since
        )
    ]


def log_cursor(session: SessionRef) -> str | None:
    """How far the platform's own reader has got. None = nothing yet."""
    return event_spool.read_cursor(_spool_of(session))


def acknowledge_log(session: SessionRef, *, through: str) -> None:
    """Everything up to and including ``through`` has been landed."""
    event_spool.write_cursor(_spool_of(session), through)


def expire_log(session: SessionRef, *, older_than_s: float) -> int:
    """Drop events past the platform's retention — the only deletion there is."""
    return event_spool.prune(_spool_of(session), older_than_s=older_than_s)


@dataclass
class ActivityTracker:
    """Shared clock for one active period of an interactive session.

    Hook arrivals and transport-specific activity probes both touch this clock.
    It belongs to the screen subscription, independently of whichever work id
    attributes the blocks produced while the session is active.
    """

    last_at: float
    suspect_since: float | None = None
    #: The one state a hook stream states outright: a tool is running. Set on
    #: `PreToolUse`; the first hook of any other kind afterwards means the tool
    #: returned (the model cannot emit anything else while a tool is in flight).
    #: `PostToolUse` is the usual one, but reading "anything else" keeps this
    #: right when a tool fails, since `PostToolUseFailure` is not subscribed.
    tool_started_at: float | None = None
    tool_returned_at: float | None = None
    #: Last hook that means work moved: a tool about to run, or the turn ending.
    #: Seeded by the monitor, since callers build this with `last_at` alone.
    last_progress_at: float | None = None
    #: Last hook that means the assistant said something. On its own it proves
    #: nothing about work; against `last_progress_at` it is the whole signal.
    last_output_at: float | None = None
    #: When the wall-clock ceiling was crossed, if it was. Recorded, not acted
    #: on: the ceiling stopped being a verdict and became a fact worth logging.
    ceiling_crossed_at: float | None = None

    def touch(self, at: float) -> None:
        self.last_at = at
        self.suspect_since = None

    def saw_hook(self, name: str, at: float) -> None:
        """Advance every clock for one hook: the in-tool state, the progress and
        output clocks, and plain activity."""
        if name == "PreToolUse":
            self.tool_started_at = at
        elif self.in_tool:
            self.tool_returned_at = at
        if name in _PROGRESS_HOOKS:
            self.last_progress_at = at
        elif name in _OUTPUT_HOOKS:
            self.last_output_at = at
        self.touch(at)

    @property
    def in_tool(self) -> bool:
        started, returned = self.tool_started_at, self.tool_returned_at
        return started is not None and (returned is None or returned < started)


#: The hooks that mean work moved. `PreToolUse` is a tool about to run,
#: `PostToolUse` is one that came back (a 40-minute command returning IS
#: progress, and the model's next line after it must not look like a session
#: that has done nothing since), `Stop` is the turn finishing on its own.
#: Nothing else counts, and assistant output least of all: a session wedged in
#: a loop produces exactly that.
_PROGRESS_HOOKS = frozenset({"PreToolUse", "PostToolUse", "Stop"})
#: The hook that means the assistant produced text.
_OUTPUT_HOOKS = frozenset({"MessageDisplay"})


@dataclass
class HookDelivery:
    """A hook translated by the screen-lifetime consumer."""

    hook: dict
    event: AgentEvent | None
    eid: str | None = None


@dataclass
class WorkAttribution:
    """Attribution for platform-requested work on a long-lived hook stream."""

    work_id: uuid.UUID
    queue: asyncio.Queue[HookDelivery]
    platform_unsolicited: bool = False
    consumer_owned: bool = False
    seen_messages: set[str] = field(default_factory=set)


@dataclass
class SessionActivity:
    """Lifecycle state for the subscription's currently active screen."""

    work_id: uuid.UUID
    queue: asyncio.Queue[HookDelivery]
    ready: bool | None
    task: asyncio.Task[None] | None = None


@dataclass
class TopicSubscription:
    """Provider-owned state whose lifetime matches one interactive screen."""

    project_id: uuid.UUID
    topic_id: uuid.UUID
    sink: HookSink
    current_work: WorkAttribution | None = None
    activity: SessionActivity | None = None
    consumer_task: asyncio.Task[None] | None = None
    ready: asyncio.Event = field(default_factory=asyncio.Event)
    # Crash-recovery replay bookkeeping, set up by ChatService before the
    # consumer starts. `replay_queue` is the spool's unread tail in order, and
    # the cursor only advances over the longest prefix that has actually been
    # persisted — so one event whose persist raised is retried on the next read
    # instead of being stepped over by the ones behind it.
    replaying: bool = False
    replay_queue: list[tuple[str, str]] = field(default_factory=list)
    replay_done: set[str] = field(default_factory=set)
    replay_seen_messages: set[str] = field(default_factory=set)
    # Reassembles the screen's MessageDisplay flushes into whole messages.
    # Subscription-scoped on purpose: its dedup memory (message ids already
    # assembled) has to survive across works, or a flush redelivered after
    # its turn ended would land again as a fragment.
    assembler: MessageAssembler = field(default_factory=MessageAssembler)


def _advance_replay_cursor(subscription: TopicSubscription) -> None:
    """Move the spool cursor over every replayed event that has landed.

    Stops at the first one that has not, which is the whole point: the cursor
    means "everything up to here reached the timeline", and a gap under it is
    an event nothing will ever read again.
    """
    reached: str | None = None
    while subscription.replay_queue:
        name, eid = subscription.replay_queue[0]
        if eid not in subscription.replay_done:
            break
        reached = name
        subscription.replay_queue.pop(0)
    if not subscription.replay_queue:
        # The replay is over and the screen may live for hours. Nothing after
        # this is a replayed event, so keeping their ids is a set that only
        # ever grows.
        subscription.replay_done.clear()
    if reached is not None and subscription.replaying:
        acknowledge_log(
            SessionRef(subscription.project_id, subscription.topic_id), through=reached
        )


# How many consumed hook ids a topic remembers (see ``_consumed_hooks``). A
# reconnect replays the spool's unread tail, and the tail is bounded by how
# long the live path has been landing events without acknowledging them —
# hours of a busy screen fit in this.
_CONSUMED_HOOKS_KEPT = 4000

# How often a suspected-wedged session re-checks liveness while it stays idle (a
# single ``confirm_alive`` at the 5-minute mark isn't enough — the screen could
# die at minute 6 and go unnoticed until the 3-hour hard ceiling otherwise).
# Cheap by design (e.g. a tmux capture-pane / list-panes call), so a short
# cadence costs nothing.
CONFIRM_POLL_S = 15.0


async def monitor_session_activity(
    *,
    queue: "asyncio.Queue[dict] | asyncio.Queue[HookDelivery]",
    idle_suspect_s: float,
    hard_ceiling_s: float,
    unread_since: Callable[[], float | None] | None = None,
    unread_grace_s: float = 0.0,
    no_progress_s: float = 0.0,
    resume_session_id: str | None,
    timeout_message: str,
    delivery_timeout_s: float = DELIVERY_TIMEOUT_S,
    delivery_message: str = UNDELIVERED_MESSAGE,
    tracker: ActivityTracker | None = None,
    confirm_alive: Callable[[], Awaitable[bool]] | None = None,
    confirm_poll_s: float = CONFIRM_POLL_S,
    on_hook: Callable[[dict], None] | None = None,
    context: str = "",
) -> AsyncIterator[AgentEvent]:
    """Monitor one active session period until ``Stop`` or a watchdog verdict.

    Both transports use this same hook clock after their transport-specific
    screen setup and prompt injection.

    Two layers replace the old single static deadline (turn 活跃度检测, review:
    a static ``deadline - now()`` can't tell "still working" from "wedged"):

    - ``idle_suspect_s``: below this much idle time (no hook AND, if ``tracker``
      is fed by a backend-specific side channel, no other activity signal) a
      session is normal. Past it the session is only suspected wedged;
      ``confirm_alive``
      (if given) is polled every ``confirm_poll_s`` until it says the screen is
      actually dead, or activity resumes and clears the suspicion.
    - ``hard_ceiling_s``: an unconditional backstop regardless of activity, so a
      pathologically active session (a tool retrying forever, a real infinite
      loop that keeps printing) still can't run forever.

    A third check, on a different axis from those two. Both of the above ask
    whether the session is producing anything. ``unread_since`` asks whether it
    is still CONSUMING: it reports when the oldest message we injected and never
    saw consumed was written. A session that has stopped reading its input keeps
    producing output, so idle-suspect never fires and ``confirm_alive`` keeps
    saying yes, while everything typed at it queues up behind a prompt box that
    will not take it. That failure is narrower than the other two, because it
    only exists while something is actually waiting, and it is the one with a
    person on the other end. ``unread_grace_s`` of 0 disables it.

    With no ``tracker``/``confirm_alive`` given (the device backend today) and
    ``idle_suspect_s == hard_ceiling_s``, this reduces to exactly the old
    single-deadline behaviour.

    The subscription owns the queue lifecycle; this function only observes its
    activity stream."""
    now = asyncio.get_event_loop().time
    start = now()
    hard_deadline = start + hard_ceiling_s
    tracker = tracker if tracker is not None else ActivityTracker(last_at=start)
    if tracker.last_progress_at is None:
        tracker.last_progress_at = start
    # Until something comes back, we have no evidence the prompt was received at
    # all: it is typed into a terminal, and typing has no return value. So the
    # first wait is short. Any hook clears it — `UserPromptSubmit` is the direct
    # receipt, and any other activity proves delivery just as well.
    delivered = False
    delivery_deadline = start + delivery_timeout_s

    def progress_verdict(t: float) -> AgentResult | None:
        """Is the session talking without working?

        The shape is two clocks against each other. `last_output_at` says the
        session is ACTIVE right now: output within the idle threshold, so this
        is the half of the space the idle check does not own. A session that
        has gone quiet belongs to the probe, whatever it said before it went
        quiet, and this verdict never touches it. `last_progress_at` says when
        work last moved: a tool starting, a tool returning, or the turn ending.
        Active for that long with nothing moving is a loop.

        A long foreground command never trips this. It emits no output while it
        runs, so the first clock is stale and the session reads as quiet, which
        is the probe's business. The earlier form of this check, "any output
        since the last progress", let a single line spoken before a long
        silence count as talking for the whole silence, and ended sessions the
        probe had already judged alive.

        Asked at the same two moments as `unread_verdict` and for the same
        reason: after a hook has been consumed the clocks are fresh, and after a
        wait has run out the queue is known to be empty.
        """
        if no_progress_s <= 0:
            return None
        output_at, progressed_at = tracker.last_output_at, tracker.last_progress_at
        if output_at is None or progressed_at is None:
            return None
        if t - output_at >= idle_suspect_s:
            return None
        if t - progressed_at < no_progress_s:
            return None
        logger.warning(
            "output for %.0fs with no tool call or ending — the session is "
            "talking and not working; ending it (%s)",
            t - progressed_at,
            context,
        )
        return AgentResult(
            text=timeout_message,
            session_id=resume_session_id,
            is_error=True,
            failure_code=TURN_TIMEOUT_CODE,
        )

    def unread_verdict(t: float) -> AgentResult | None:
        """Has an injected message gone unread past its grace, at a moment the
        session could have read it?

        Asked in two places and deliberately not at the top of the loop: after a
        hook has been consumed (so `in_tool` reflects it) and after a wait has
        run out (so the queue is known to be empty). At the loop top a queued
        `PreToolUse` has not been read yet, and the verdict would fire on a
        session that is, one line later, discovered to be inside a tool.

        Three gates on it. `delivered`: until the session has taken its first
        prompt there is no unread injection, only an undelivered prompt, which
        has its own verdict. `in_tool`: input is read at tool boundaries, so
        while a tool is in flight the clock does not run at all. And the clock
        starts from the tool's RETURN when there was one, not from the
        injection: a message that sat behind a 40-minute command gets its grace
        after the first boundary at which the session could see it.
        """
        if not (delivered and unread_grace_s > 0 and unread_since is not None):
            return None
        if tracker.in_tool:
            return None
        waiting_since = unread_since()
        if waiting_since is None:
            return None
        if tracker.tool_returned_at is not None:
            waiting_since = max(waiting_since, tracker.tool_returned_at)
        if t - waiting_since < unread_grace_s:
            return None
        logger.warning(
            "an injected message went unread for %.0fs — the session is "
            "producing but not consuming; ending it (%s)",
            t - waiting_since,
            context,
        )
        return AgentResult(
            text=delivery_message,
            session_id=resume_session_id,
            is_error=True,
            failure_code=PROMPT_UNDELIVERED_CODE,
        )

    while True:
        t = now()
        if t >= hard_deadline:
            # Recorded, not acted on. Elapsed time alone cannot tell an agent
            # three hours into a refactor from a session that is stuck, and the
            # three gates below each catch a specific way of being stuck: the
            # process gone (`confirm_alive`), output with no tool call
            # (`no_progress_s`), input never consumed (`unread_grace_s`). What
            # is left for a wall clock to end is a turn that is working and has
            # not finished, which is not a fault. It stays as a fact: logged
            # here, kept on the tracker, and the next interval starts.
            if tracker.ceiling_crossed_at is None:
                tracker.ceiling_crossed_at = t
            logger.warning(
                "session past its %.0fs ceiling and still going — recorded, not "
                "ended (%s)",
                t - start,
                context,
            )
            hard_deadline = t + hard_ceiling_s
        # Asked before the waits below, because this is the one verdict that can
        # be true while every other signal looks healthy.
        if delivered:
            idle_for = t - tracker.last_at
            if idle_for >= idle_suspect_s:
                wait_for = min(confirm_poll_s, hard_deadline - t)
            else:
                wait_for = min(idle_suspect_s - idle_for, hard_deadline - t)
        else:
            wait_for = min(hard_deadline - t, delivery_deadline - t)
        try:
            delivery = await asyncio.wait_for(queue.get(), timeout=max(wait_for, 0.01))
        except TimeoutError:
            if not delivered:
                if now() >= delivery_deadline:
                    logger.warning(
                        "no hook within %.0fs of the prompt — ending as "
                        "undelivered; the claude may still hold it queued (%s)",
                        delivery_timeout_s,
                        context,
                    )
                    yield AgentResult(
                        text=delivery_message,
                        session_id=resume_session_id,
                        is_error=True,
                        failure_code=PROMPT_UNDELIVERED_CODE,
                    )
                    return
                continue
            verdict = progress_verdict(now()) or unread_verdict(now())
            if verdict is not None:
                yield verdict
                return
            idle_for = now() - tracker.last_at
            if idle_for >= idle_suspect_s:
                if tracker.suspect_since is None:
                    tracker.suspect_since = now()
                alive = await confirm_alive() if confirm_alive is not None else True
                if not alive:
                    logger.warning(
                        "screen declared dead after %.0fs idle — ending as "
                        "timeout (%s)",
                        idle_for,
                        context,
                    )
                    yield AgentResult(
                        text=timeout_message,
                        session_id=resume_session_id,
                        is_error=True,
                        failure_code=TURN_TIMEOUT_CODE,
                    )
                    return
            continue
        hook = delivery.hook if isinstance(delivery, HookDelivery) else delivery
        if on_hook is not None:
            on_hook(hook)
        delivered = True
        tracker.saw_hook(_hook_event_name(hook), now())
        event = (
            delivery.event
            if isinstance(delivery, HookDelivery)
            else translate_hook(delivery)
        )
        if event is not None:
            yield event
            if isinstance(event, AgentResult):
                return  # Stop hook → session idle
        verdict = progress_verdict(now()) or unread_verdict(now())
        if verdict is not None:
            yield verdict
            return


def _is_mid_response(events: list[AgentEvent]) -> bool:
    """Does this hook prove the session is PART-WAY THROUGH a response?

    An activity is what the room reads as 正在处理, and the only thing that
    retires one on the ordinary path is the session's own ``Stop``. So it may
    only be opened by something a ``Stop`` is guaranteed to follow — the agent
    producing output. That is the whole rule, and it is not a list of hook
    names: a hook type added later is covered by it without being enumerated.

    A hook that merely HAPPENED is not that. A session coming up
    (``SessionStart``, which fires again on every resume and every auto-compact),
    a tool returning after the answer was already given, a prompt being typed —
    each of those used to light the room and then had nothing left to take it
    down, because no ``Stop`` was coming. The mark then stood until the hard
    ceiling three hours later, reasserted onto every reconnecting client by the
    ``turn_active`` snapshot: 「芝士正在处理…」 in a room where nobody was working,
    which no amount of reloading could clear.

    A batch that carries the ending is not an opening either: nothing is
    in-flight after a ``Stop``, and opening on it only to close it two lines
    later would flash the indicator for a response already finished.
    """
    if any(isinstance(event, AgentResult) for event in events):
        return False
    return any(
        isinstance(event, AgentMessage | AgentToolUse | AgentToolResult)
        for event in events
    )


class ScreenSetupError(Exception):
    """A backend couldn't bring the screen to a prompt-ready state (no Docker /
    no online device / not ready in time). Its message becomes the work error
    result — the ONE place setup failures turn into an ``AgentResult``.

    ``failure_code`` is set when the platform already knows WHICH failure this
    is (`platform_failures`). Left None for the setup failures it has no
    classification for, which then land as an unnamed turn error — the same
    place they landed before, but by omission rather than by a sentence not
    matching."""

    def __init__(self, message: str, *, failure_code: str | None = None) -> None:
        super().__init__(message)
        self.failure_code = failure_code


def _image_paths(images: list[dict] | None) -> list[str]:
    return list(
        dict.fromkeys(
            str(image.get("path") or "").strip()
            for image in images or []
            if str(image.get("path") or "").strip()
        )
    )


def _prompt_with_native_images(
    prompt: str, images: list[dict] | None, missing: list[dict] | None = None
) -> str:
    """Use Claude Code's own @path attachment path for interactive sessions.

    Verified end to end on 2.1.224: a bracketed paste containing `@uploads/x.png`
    collapses into a `[Pasted text]` widget, and submitting it still resolves the
    mention — the request that goes out carries a real image block. So the
    mention is the delivery, and it only works for a file that is actually on
    the machine the screen is running on.

    ``missing`` is for the ones that are not. They get a sentence instead of a
    mention, because the alternative shapes are both worse: @-mentioning a path
    that is not there produces nothing at all, and saying nothing leaves 芝士
    answering a question about a picture it was never shown, with no way to know
    that is what it is doing.
    """
    paths = _image_paths(images)
    lost = _image_paths(missing)
    parts = [prompt] if prompt else []
    if paths:
        parts.append("\n".join(f"@{path}" for path in paths))
    if lost:
        named = "、".join(lost)
        parts.append(
            f"【平台】本轮有 {len(lost)} 张图片没能送到这台机器上（{named}），"
            "你手上没有它们的内容。回复时直说没收到图，不要猜图里是什么。"
        )
    return "\n\n".join(parts)


class SpoolBacklog:
    """The unread tail of one session's hook spool, ready to be landed.

    Everything Claude-Code-shaped about backfilling lives here: that the log is
    a directory of hook files, that a message arrives as several MessageDisplay
    flushes and only the assembler knows when it is whole, that an entry has to
    carry its own id into the payload before translation. The platform loops
    over `unread()` deciding what to persist; it never sees a hook.

    Built per pass, because the assembler is: its buffer of half-arrived
    messages belongs to this reading, and a flush that never completes must not
    leak into the next one.
    """

    def __init__(self, session: SessionRef) -> None:
        self._session = session
        self._assembler = MessageAssembler()
        # Snapshot at construction: landing things as the pass goes must not
        # change what this pass was asked to land.
        self._unread = read_log(session, since=log_cursor(session))

    def unread(self) -> list[HarnessEvent]:
        return self._unread

    def assemble(self, entry: HarnessEvent) -> list[AgentEvent]:
        if not isinstance(entry.record, dict):
            return []
        payload = dict(entry.record)
        payload["_eid"] = entry.eid
        # What was said WHEN it was said. A backfill pass runs long after the
        # fact, so without this every recovered message would be stamped "now"
        # and sort to the bottom of a conversation it belongs in the middle of.
        payload[RECORDED_AT_KEY] = datetime.now(UTC) - timedelta(
            seconds=max(entry.age_s, 0.0)
        )
        return self._assembler.translate(payload)

    def unfinished(self) -> set[str]:
        return self._assembler.pending_eids()

    def give_up(self) -> list[AgentMessage]:
        return list(self._assembler.drain())

    def landed(self, *, through: str) -> None:
        acknowledge_log(self._session, through=through)

    def forget(self, *, older_than_s: float) -> None:
        expire_log(self._session, older_than_s=older_than_s)


class Channel:
    """一条通往「机器上一块屏幕」的通道：开机器、把字送进去、按 Escape。

    The transport half of what used to be one class. A channel knows how to
    reach a machine and how to type on it, and nothing about what is running
    there — the subscription, activity, spool and receipt machinery that every
    transport used to inherit a copy of is written once above this seam, in
    ``ClaudeCodeRuntime``.

    Two methods have no sensible default and every channel writes them:
    ``ensure_ready`` (bring a screen up) and ``send_prompt`` (put text into it).
    The rest default to 「这条通道没有这个能力」, which is what lets a channel be
    as small as the transport actually is.
    """

    # WHICH machine pool this is: what a topic's ``compute_profile`` stores and
    # what the market board lists. Deliberately not the runtime's ``harness`` —
    # this says which machine, that says what runs on it.
    name: str = "channel"

    # 图片输入: whether the bytes actually reach the session on this transport.
    # The runtime @-mentions the path and Claude Code resolves it into a native
    # image block — true for a local screen that already shares the worktree and
    # for a device that stages the file first, and a transport where neither
    # holds MUST say False rather than let the prompt promise an image 芝士
    # cannot see.
    embeds_images: bool = True

    async def stage_images(
        self, screen: object, images: list[dict]
    ) -> tuple[list[dict], list[dict]]:
        """Put these images where the screen can open them, as
        ``(reachable, unreachable)``.

        The default answers for every transport whose screen already shares the
        worktree the upload was written to: all of them, nothing lost.

        Splitting this out of ``send_prompt`` is the point. When staging lived
        inside the send, a transport that could not stage — an older connector
        that does not know the file frame, a machine that is briefly
        unreachable — raised out of the send and took the ENTIRE message with
        it, text included. Measured 2026-08-23: a message with one screenshot
        left no trace in the room at all, while plain-text messages around it
        arrived normally. An image that cannot be delivered must cost the image.
        """
        del screen
        return list(images), []

    # Does a turn here have to wait for a machine to be created first? The turn
    # path branches on it (``ChatService`` shows 「机器正在创建」 and holds the
    # prompt) rather than on the channel's class, so a second leased-machine
    # transport gets the same waiting room without the platform learning its
    # name.
    provisions_machine: bool = False

    # Does this channel assemble the machine's model environment itself? True
    # means the platform sends the model CHOICE and nothing else: no base_url,
    # no provider credential, no alias pins — the channel builds them where the
    # machine is, and the turn's supply route is the deployment's (the metering
    # proxy under a subscription, /llm otherwise). False means the channel is
    # handed the resolved profile env instead.
    #
    # A capability, not a name. The turn path decided this by NAME twice, and
    # each time the name aged into a list of the channels that happened to have
    # the trick on the day it was written: the next channel to learn it — or to
    # inherit it wholesale — was never added. Nothing failed loudly when that
    # happened. The turn ran, on the wrong supply's meter and with a --model
    # flag the supply does not serve, and only the invoice said so.
    #
    # Any channel whose machine is not this process MUST say True. Saying False
    # ships it ``profile.full_env()`` — a base_url only this box can resolve and
    # a real provider key — onto hardware over a network, which is the leak the
    # split exists to prevent.
    builds_model_env: bool = False

    # How big the machine behind this channel is, when we are the ones who set
    # it. None means the channel genuinely does not know — an enrolled machine
    # belongs to someone else — and the prompt then says nothing rather than
    # inventing a limit the agent would plan around.
    sandbox_memory_mb: int | None = None
    sandbox_cores: int | None = None

    # Copy only. Said when a turn arrives with no topic, and when one times out
    # — a timeout is classified by the code the result carries, so a channel may
    # word these however it likes.
    needs_topic_message: str = "本轮需要话题上下文"
    timeout_message: str = TURN_TIMEOUT_MESSAGE

    def available(self) -> bool:
        return True

    async def prepare_topic(
        self,
        *,
        project_id: uuid.UUID,
        topic_id: uuid.UUID,
        actor: object | None,
    ) -> tuple[bool, str]:
        """Get the machine ready before the turn counts a delivery attempt.

        Only asked of a channel that declares ``provisions_machine``. The
        default is the answer for every transport whose machine is already
        there: ready, nothing to say about it.
        """
        del project_id, topic_id, actor
        return True, ""

    async def discover(
        self, device_id: str | None = None
    ) -> list[tuple[uuid.UUID, uuid.UUID, object | None, str | None]]:
        """Screens of ours that survived this process, as
        ``(project_id, topic_id, screen, running)``. ``screen`` is None when the
        channel knows the topic is still out there but cannot hand back a handle
        for it yet (the device transport reattaches on the next turn).

        ``running`` is the tag the screen was started with, handed back unread:
        one machine can host sessions of more than one harness, and only the
        runtime knows which tag is its own. None means this channel cannot tell
        — and a channel that cannot tell cannot host two harnesses at once,
        because nothing is left to stop one from claiming the other's screens.

        The runtime subscribes to what this returns. A channel that answers
        nothing simply has nothing that outlives the backend.
        """
        del device_id
        return []

    def topics_on_device(self, device_id: str) -> list[uuid.UUID]:
        """Topics whose screen lives on the device that just went away."""
        del device_id
        return []

    def forget_topic(self, topic_id: uuid.UUID) -> None:
        """Drop whatever this channel remembers about a topic being torn down."""
        del topic_id

    async def precheck(self, project_id: uuid.UUID, topic_id: uuid.UUID) -> object:
        """Cheap fail-fast checks that run BEFORE the token is minted and the
        hook queue is claimed — a turn that cannot run at all must never touch
        the router. Raise ``ScreenSetupError`` to end the turn with a clean
        error result. The return value is handed to ``ensure_ready`` as
        ``precheck`` so a channel doesn't resolve twice (the device channel
        resolves its pinned device here)."""
        return None

    async def ensure_ready(
        self,
        *,
        project_id: uuid.UUID,
        topic_id: uuid.UUID,
        token: str,
        env: dict[str, str] | None,
        memory_scope: str | None,
        owner: str | None,
        turn_id: uuid.UUID | None,
        launch: LaunchPlan,
        precheck: object,
    ) -> object:
        """Bring the topic's screen to a prompt-ready state; raise
        ``ScreenSetupError`` if it can't be. Implemented by every channel.

        ``launch`` is what to run. A channel does not build it and does not read
        it: it says where this screen keeps its state and what its cwd is
        (``launch.at(ScreenPlace(...))``), plants the files that come back,
        starts the command. Which harness that turns out to be is the caller's
        business — a channel that decided could only ever host the one.

        It is a launch-time input, not a per-prompt one. The system prompt
        inside it reaches the session through a file read exactly once at exec,
        so a screen that is merely reused keeps the one it was started with, and
        the plan matters only on the call that turns out to be a cold start."""
        raise NotImplementedError

    async def send_prompt(self, screen: object, prompt: str) -> bool | None:
        """Deliver the turn's prompt to the ready screen.

        Returns the screen's readiness at delivery time when the transport can
        know it (the device connector answers ``{ready: bool}``): ``False``
        means the prompt is HELD until the session can take it — worth a visible
        line in the room instead of silence (#445). ``None`` = unknown."""
        raise NotImplementedError

    async def start_activity_monitor(
        self, screen: object, tracker: ActivityTracker
    ) -> asyncio.Task | None:
        """Optional background activity signal alongside hook arrivals (e.g. the
        tmux backend's capture-pane polling — a long tool call between hooks
        must still count as "alive"). Return a task that keeps ``tracker``
        touched; ``run_turn`` cancels it when the turn ends.

        Default: no extra signal, activity is judged from hook arrivals alone —
        correct for the device channel today (TODO: an equivalent remote
        activity probe, e.g. ``device_hub`` screen bytes, is future work; see
        ``DeviceChannel``)."""
        return None

    async def send_interrupt(self, screen: object) -> bool:
        """Stop whatever this screen is doing, without saying anything. False =
        this transport has no way to.

        Default: no. Escape is a KEY, and a transport that can put a prompt into
        a session cannot necessarily press one — so a channel that has not said
        it can must answer no rather than raise, or the runtime's ``interrupt``
        turns a missing capability into a crash.
        """
        del screen
        return False

    async def confirm_alive(self, screen: object) -> bool:
        """Called (repeatedly, while idle persists) once the idle-suspect
        threshold is crossed, to confirm the screen isn't actually dead before
        treating the idle window as fatal. Default: assume alive — no cheap
        probe exists at this level. A channel that can ask its transport
        cheaply overrides this."""
        return True


class ClaudeCodeRuntime:
    """Claude Code, driven over one ``Channel``.

    Everything here is a cost of driving a TUI written for a person — a spool of
    hook files because there is no event API, a subscription per topic to read
    it, an activity watch because a session that goes quiet is indistinguishable
    from one that died, a receipt for every prompt because the write and the
    consumption are minutes apart. None of it is a fact about the transport, and
    that is the point: it is written ONCE here, over whatever channel the
    compute side opened.

    It used to be a base class the transports inherited, so every one of those
    mechanisms existed once per transport, and the only way to add a second
    harness was to write it once per transport too. Composition is what makes
    that M×N an M+N: a channel implements ``ensure_ready`` and ``send_prompt``,
    and knows nothing about hooks.
    """

    def __init__(
        self,
        channel: Channel,
        *,
        router: HookRouter | None = None,
        idle_suspect_s: float = 900.0,
        hard_ceiling_s: float = 900.0,
        session_ceiling_s: float | None = None,
        unread_grace_s: float = 0.0,
        no_progress_s: float = 0.0,
        delivery_timeout_s: float = DELIVERY_TIMEOUT_S,
    ) -> None:
        self._channel = channel
        self._router = router or hook_router
        # Equal by default preserves the legacy single-deadline behavior.
        self._idle_suspect_s = idle_suspect_s
        self._hard_ceiling_s = hard_ceiling_s
        # Two ceilings, because the two layers can do different things when they
        # come due and so they want different numbers.
        #
        # `_hard_ceiling_s` is what the OUTER wall-clock wrap is told (see the
        # `hard_ceiling_s` property). That layer sees only frames, has no way to
        # ask whether a session is alive, and refreshes on every tool call, so
        # what it ends is a turn that has stopped calling tools: exactly a loop
        # that only emits output.
        #
        # `_session_ceiling_s` is this monitor's own unconditional backstop, and
        # it fires against a session that idle-suspect has been probing and
        # `confirm_alive` keeps calling alive. That combination is a busy pane
        # with no interim hook, which is what a long foreground command looks
        # like, so cutting it at the same number would kill exactly the work the
        # probe just confirmed was fine. It stays large on purpose: past this
        # much wall clock the answer stops being "still working" whatever the
        # probe says.
        self._session_ceiling_s = (
            hard_ceiling_s if session_ceiling_s is None else session_ceiling_s
        )
        self._unread_grace_s = unread_grace_s
        self._no_progress_s = no_progress_s
        self._unread_probe: UnreadProbe | None = None
        self._delivery_timeout_s = delivery_timeout_s
        # Screen-lifetime state. ``_live`` is the transport handle; subscriptions
        # own the stable router sink, consumer task, and current attribution.
        self._live: dict[uuid.UUID, object] = {}
        self._subscriptions: dict[uuid.UUID, TopicSubscription] = {}
        # Hook event ids this process has already consumed, per topic. A hook
        # can reach the consumer twice — live over /sandbox/hooks and again out
        # of the spool when a reconnect replays it, in either order — and the
        # second copy must not be translated again. Kept on the runtime, not
        # the subscription: a reconnect drops and recreates the subscription,
        # and the whole point is to remember across that.
        self._consumed_hooks: dict[uuid.UUID, dict[str, None]] = {}
        self._event_consumer: EventConsumer | None = None
        self._activity_consumer: ActivityConsumer | None = None
        self._delivery_locks: dict[uuid.UUID, asyncio.Lock] = {}
        # Every UserPromptSubmit is reported here (#539 decision A): the
        # transport write is delivery — this hook is the CONSUMPTION record,
        # which is when the consumed stamp belongs, however late it fires.
        self._receipt_consumer: ReceiptConsumer | None = None
        self._receipt_tasks: set[asyncio.Task[None]] = set()
        _RUNTIMES.add(self)

    @property
    def hard_ceiling_s(self) -> float:
        """This runtime's absolute active-session ceiling."""
        return self._hard_ceiling_s

    @property
    def channel(self) -> Channel:
        """The transport this runtime is driving. Wiring reads it (which pool
        is this, can it run at all); the turn path never does."""
        return self._channel

    @property
    def name(self) -> str:
        """Which machine pool — the channel's answer, not the runtime's."""
        return self._channel.name

    @property
    def embeds_images(self) -> bool:
        return self._channel.embeds_images

    async def _stage(
        self, screen: object, images: list[dict] | None
    ) -> tuple[list[dict], list[dict]]:
        """Ask the channel to put this turn's images where the screen can open
        them, and never let that question fail the send.

        A channel is expected to report per-image losses rather than raise, but
        it talks to a machine over a network and this is the last place that can
        still choose between "the message arrives without its picture" and "the
        message does not arrive". It is the first every time.
        """
        if not images:
            return [], []
        try:
            return await self._channel.stage_images(screen, images)
        except Exception:  # noqa: BLE001 — an image is never worth the message
            logger.exception("staging this turn's images failed")
            return [], list(images)

    @property
    def sandbox_memory_mb(self) -> int | None:
        return self._channel.sandbox_memory_mb

    @property
    def sandbox_cores(self) -> int | None:
        return self._channel.sandbox_cores

    @property
    def provisions_machine(self) -> bool:
        return self._channel.provisions_machine

    @property
    def builds_model_env(self) -> bool:
        return self._channel.builds_model_env

    def available(self) -> bool:
        return self._channel.available()

    async def prepare_topic(
        self,
        *,
        project_id: uuid.UUID,
        topic_id: uuid.UUID,
        actor: object | None,
    ) -> tuple[bool, str]:
        return await self._channel.prepare_topic(
            project_id=project_id, topic_id=topic_id, actor=actor
        )

    def bind_events(self, consumer: EventConsumer) -> None:
        """Bind the room persistence and broadcast callback owned by ChatService."""
        self._event_consumer = consumer

    def bind_receipts(self, consumer: ReceiptConsumer) -> None:
        """Bind the owner of prompt receipts: every UserPromptSubmit the
        screen emits is reported as ``(topic_id, prompt_text)`` — ChatService
        matches it against messages it injected and stamps them consumed."""
        self._receipt_consumer = consumer

    def bind_unread_probe(self, probe: UnreadProbe) -> None:
        """Bind the other end of the same books: what was injected and never
        came back as a receipt. The monitor asks per topic while a session
        runs."""
        self._unread_probe = probe

    def _unread_since_for(
        self, topic_id: uuid.UUID
    ) -> Callable[[], float | None] | None:
        """Bind the probe to one topic, so the monitor can ask without knowing
        which topic it is watching."""
        probe = self._unread_probe
        if probe is None:
            return None
        return lambda: probe(topic_id)

    def bind_activity(self, consumer: ActivityConsumer) -> None:
        """Bind the room's session-activity lifecycle callback."""
        self._activity_consumer = consumer

    async def deliver(
        self, topic_id: uuid.UUID, text: str, images: list[dict] | None = None
    ) -> bool:
        """Inject ``text`` into the topic's active screen.

        This is what lets a message posted mid-turn reach 芝士 now instead of
        queueing behind the whole turn. It works because the thing on the other
        end is an interactive Claude Code, which accepts input while it is
        working and folds it into the run (measured: a prompt pasted into a busy
        session was answered without waiting for the running command). The
        platform used to be stricter than the tool it drives — one message per
        topic per turn — so a long command made every later message wait it out.

        Deliberately does not create a screen. ``False`` tells the caller to
        construct and inject fresh work through the normal path."""
        lock = self._delivery_locks.setdefault(topic_id, asyncio.Lock())
        async with lock:
            screen = self._live.get(topic_id)
            subscription = self._subscriptions.get(topic_id)
            if (
                screen is None
                or subscription is None
                or subscription.current_work is None
            ):
                logger.info(
                    "mid-turn delivery skipped (topic=%s): no live screen here "
                    "(screen=%s subscription=%s work=%s) — falling back to a "
                    "fresh turn",
                    topic_id,
                    screen is not None,
                    subscription is not None,
                    subscription is not None and subscription.current_work is not None,
                )
                return False
            staged, lost = await self._stage(screen, images)
            delivered_text = _prompt_with_native_images(text, staged, lost)
            try:
                await self._channel.send_prompt(screen, delivered_text)
            except ScreenSetupError as exc:
                logger.warning(
                    "mid-turn delivery failed at screen setup (topic=%s): %s",
                    topic_id,
                    exc,
                )
                return False
            except Exception:  # noqa: BLE001 — failed inject falls back to a turn
                logger.exception(
                    "deliver into running turn failed (topic=%s)", topic_id
                )
                return False
            # The write was accepted — that IS delivery (#539 decision A, per
            # #487's transport contract: a write either reaches the process or
            # errors). UserPromptSubmit fires when the session CONSUMES the
            # message — often much later on a busy session — so it must never
            # gate this verdict; it arrives through _observe_delivery_hook and
            # stamps the message consumed then.
            return True

    def _observe_delivery_hook(self, topic_id: uuid.UUID, hook: dict) -> None:
        event_name = str(hook.get("hook_event_name") or hook.get("hookEventName") or "")
        if event_name != "UserPromptSubmit":
            return
        prompt = hook.get("prompt")
        if not isinstance(prompt, str):
            return
        logger.info(
            "prompt receipt: session consumed an input (topic=%s, %d chars)",
            topic_id,
            len(prompt),
        )
        consumer = self._receipt_consumer
        if consumer is None:
            return

        async def _report() -> None:
            await consumer(topic_id, prompt)

        task = asyncio.create_task(_report())
        self._receipt_tasks.add(task)
        task.add_done_callback(self._receipt_tasks.discard)

    async def ensure_subscription(
        self,
        project_id: uuid.UUID,
        topic_id: uuid.UUID,
        *,
        paused: bool = False,
    ) -> TopicSubscription:
        """Ensure one stable sink and consumer for the topic's live screen."""
        subscription = self._subscriptions.get(topic_id)
        if subscription is not None:
            return subscription
        subscription = TopicSubscription(
            project_id=project_id,
            topic_id=topic_id,
            sink=self._router.subscribe(str(topic_id)),
        )
        if not paused:
            subscription.ready.set()
        self._subscriptions[topic_id] = subscription
        subscription.consumer_task = asyncio.create_task(
            self._consume_subscription(subscription),
            name=f"hook subscription topic={topic_id}",
        )
        return subscription

    def holds(self, topic_id: uuid.UUID) -> bool:
        """Is there a screen this runtime can still reach for this topic?

        This is what "the work survived" means after a backend restart: the
        coroutine waiting on the turn died with the process, the claude in the
        execution environment did not, and `recover` found it
        again. Everything the orphan sweep used to infer from side effects — a
        block bearing the turn's id, an unread hook in the spool — was an
        attempt to answer this question without being able to ask it.
        """
        return topic_id in self._live

    async def recover(self, device_id: str | None = None) -> list[SessionRef]:
        """Listen again to the screens that outlived this process.

        The channel finds them; subscribing to them is this side's job. It used
        to be one method a transport overrode wholesale, which meant every
        transport reached back up into the subscription table to finish the
        thought — the exact upward call composition exists to remove.

        Paused, because a recovered subscription must not start replaying into
        a room before the platform has decided what to do with it.
        """
        recovered: list[SessionRef] = []
        for project_id, topic_id, screen, runs in await self._channel.discover(
            device_id
        ):
            if runs is not None and runs != self.harness:
                # Someone else's session on a machine we share. Claiming it
                # would mean translating another harness's output with this
                # one's assembler and reporting it as ours.
                continue
            await self.ensure_subscription(project_id, topic_id, paused=True)
            if screen is not None:
                self._live[topic_id] = screen
            recovered.append(SessionRef(project_id, topic_id))
        return recovered

    async def drop_device_subscriptions(self, device_id: str) -> None:
        """Drop recovered subscriptions associated with a disconnected device."""
        for topic_id in self._channel.topics_on_device(device_id):
            await self._close_topic(topic_id)

    async def replay(self, session: SessionRef, *, known_texts: set[str]) -> None:
        """Push the spool's unread tail back into a recovered subscription.

        Where the tail starts is the spool's own cursor. It used to be inferred
        — walk the topic's blocks backwards for the newest event id that also
        appears in the spool — which was a guess dressed as a fact: an event the
        live path had persisted WITHOUT an id, or a tail whose every event was
        of a kind that persists nothing, left the search empty and replayed the
        whole spool from the beginning. The cursor is the same claim, written by
        whoever actually persisted the events instead of reconstructed from
        their leftovers.

        The events go back through the SAME consumer the live path uses, so
        nothing about landing them is written twice. Until this returns the
        subscription is held paused, or a hook arriving mid-replay would be
        translated ahead of the tail it belongs behind.
        """
        subscription = self._subscriptions.get(session.topic_id)
        if subscription is None:
            return
        try:
            events = read_log(session, since=log_cursor(session))
            if not events:
                return
            subscription.replaying = True
            replayed: dict[str, dict] = {}
            for event in events:
                subscription.replay_queue.append((event.key, event.eid))
                if not isinstance(event.record, dict):
                    # Unparseable, so nothing can ever be made of it — mark it
                    # done so the cursor steps over it rather than stopping here
                    # forever.
                    subscription.replay_done.add(event.eid)
                    continue
                if event.eid in replayed:
                    continue
                replayed[event.eid] = event.record
            if replayed:
                subscription.replay_seen_messages.update(known_texts)
            for eid, payload in replayed.items():
                queued = dict(payload)
                queued["_eid"] = eid
                subscription.sink.queue.put_nowait(queued)
        finally:
            subscription.ready.set()
        await asyncio.wait_for(subscription.sink.queue.join(), timeout=30)

    async def close(self, session: SessionRef) -> None:
        """Let this session go: stop listening, forget the channel.

        Not the same as killing the screen. The conversation on the other side
        is untouched — this is the platform putting the phone down.
        """
        await self._close_topic(session.topic_id)

    async def _close_topic(self, topic_id: uuid.UUID) -> None:
        """``close`` keyed the way this adapter's own tables are keyed.

        The bulk teardowns — a device went offline, a screen died, a workspace
        was reaped — arrive holding a topic and nothing else, and inventing the
        other half of a ``SessionRef`` for them would be inventing an id. One
        conversation per topic is what makes that possible; the day a room hosts
        two agents at once, this key grows and so does theirs.
        """
        self._channel.forget_topic(topic_id)
        subscription = self._subscriptions.pop(topic_id, None)
        self._live.pop(topic_id, None)
        if subscription is None:
            return
        subscription.current_work = None
        if subscription.activity is not None:
            await self._end_session_activity(subscription, subscription.activity)
        self._router.unsubscribe(str(topic_id), subscription.sink)
        task = subscription.consumer_task
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def drop_screen_subscription(self, screen: object) -> None:
        """Drop topics whose live transport handle is this dead screen."""
        topic_ids = [
            topic_id
            for topic_id, live_screen in self._live.items()
            if live_screen is screen or live_screen == screen
        ]
        for topic_id in topic_ids:
            await self._close_topic(topic_id)

    # --- AgentRuntime -------------------------------------------------------

    # What this adapter drives. Separate from `name`, which every subclass sets
    # to its compute pool ("device" / "cloud") — that says which machine, this
    # says what runs on it.
    harness = CLAUDE_CODE

    def backlog(self, session: SessionRef) -> Backlog:
        return SpoolBacklog(session)

    async def interrupt(self, session: SessionRef) -> bool:
        """Take the work away without saying anything. Escape is what stops a
        claude mid-generation — the same key a person watching the screen would
        press, sent down the same channel that carries their typing.

        Weaker than tearing the screen down, deliberately: the session and its
        conversation survive, and the next ``send`` continues it.
        """
        screen = self._live.get(session.topic_id)
        if screen is None:
            return False
        try:
            return await self._channel.send_interrupt(screen)
        except Exception:  # noqa: BLE001 — a transport that refuses is a False
            logger.exception("interrupt failed for topic %s", session.topic_id)
            return False

    def _already_consumed(self, topic_id: uuid.UUID, hook_eid: str) -> bool:
        return hook_eid in self._consumed_hooks.get(topic_id, {})

    def _remember_consumed(self, topic_id: uuid.UUID, hook_eid: str) -> None:
        seen = self._consumed_hooks.setdefault(topic_id, {})
        seen[hook_eid] = None
        # Insertion-ordered, so trimming the front drops the oldest. Bounded
        # per topic: a screen lives for hours and every hook is one entry.
        while len(seen) > _CONSUMED_HOOKS_KEPT:
            del seen[next(iter(seen))]

    async def _consume_subscription(self, subscription: TopicSubscription) -> None:
        """Continuously translate the screen's hooks into attributed events."""
        await subscription.ready.wait()
        while True:
            hook = await subscription.sink.queue.get()
            try:
                self._observe_delivery_hook(subscription.topic_id, hook)
                attribution = subscription.current_work
                if attribution is None:
                    attribution = WorkAttribution(
                        work_id=uuid.uuid4(),
                        queue=asyncio.Queue(),
                        platform_unsolicited=True,
                        seen_messages=set(subscription.replay_seen_messages),
                    )
                    subscription.replay_seen_messages.clear()
                    subscription.current_work = attribution
                eid_value = hook.get("_eid")
                hook_eid = eid_value if isinstance(eid_value, str) else None
                if hook_eid is not None and self._already_consumed(
                    subscription.topic_id, hook_eid
                ):
                    # The same hook, delivered twice: once live and once from
                    # the spool on a reconnect (or the other way round).
                    # Persisting is idempotent by event id for what the
                    # assembler passes through, but not for what it decides
                    # FROM a hook: a Stop whose message was already flushed
                    # persists nothing the first time, so its second copy,
                    # landing in a fresh attribution that never saw the flush,
                    # posted the reply again (dev, 2026-09-02, topic 0f139cd7,
                    # two identical 芝士 messages 0.7s apart). One hook, one
                    # consumption; the replay bookkeeping still steps over it.
                    if subscription.replay_queue:
                        subscription.replay_done.add(hook_eid)
                        _advance_replay_cursor(subscription)
                    continue
                # One hook can surface zero events (a MessageDisplay flush
                # still buffering toward its message) or several (a Stop
                # draining a partial message ahead of the result); each
                # surfaced event routes exactly like the old one-hook-one-event
                # flow did.
                events = subscription.assembler.translate(hook)
                # Translated BEFORE the activity below, because what this hook
                # turned out to say is what decides whether an activity may be
                # opened at all.
                activity = subscription.activity
                if (
                    activity is None
                    and _is_mid_response(events)
                    and (attribution.platform_unsolicited or attribution.consumer_owned)
                ):
                    screen = self._live.get(subscription.topic_id)
                    if screen is not None:
                        activity = await self._begin_session_activity(
                            subscription,
                            attribution,
                            screen,
                            ready=True,
                        )
                for event in events:
                    if isinstance(event, AgentMessage):
                        attribution.seen_messages.add(event.text.strip())
                consumer_owned = (
                    attribution.platform_unsolicited or attribution.consumer_owned
                )
                if not events:
                    # No surfaced event (a buffered flush, UserPromptSubmit,
                    # PostToolUse…) still proves delivery and liveness: the
                    # eventless hook reaches the same queues it always did, so
                    # the turn monitor and the watchdog keep their clock.
                    delivery = HookDelivery(hook=hook, event=None, eid=hook_eid)
                    if activity is not None:
                        activity.queue.put_nowait(delivery)
                    if not consumer_owned:
                        attribution.queue.put_nowait(delivery)
                consumer = self._event_consumer
                consume_failed = False
                for event in events:
                    eid = (
                        event.eid
                        if isinstance(event, AgentMessage | AgentToolUse) and event.eid
                        else hook_eid
                    )
                    delivery = HookDelivery(hook=hook, event=event, eid=eid)
                    if activity is not None:
                        activity.queue.put_nowait(delivery)
                    if not consumer_owned:
                        attribution.queue.put_nowait(delivery)
                        continue
                    if consumer is None:
                        continue
                    result_text_seen = (
                        isinstance(event, AgentResult)
                        and event.text.strip() in attribution.seen_messages
                    )
                    try:
                        await consumer(
                            subscription.project_id,
                            subscription.topic_id,
                            attribution.work_id,
                            event,
                            eid,
                            result_text_seen,
                            attribution.platform_unsolicited,
                        )
                    except Exception:  # noqa: BLE001 — keep the stream alive
                        consume_failed = True
                        logger.exception(
                            "unsolicited hook persist failed (topic=%s, eid=%s)",
                            subscription.topic_id,
                            eid,
                        )
                if hook_eid is not None and not consume_failed:
                    self._remember_consumed(subscription.topic_id, hook_eid)
                replay_processed = (not events) or (
                    consumer_owned and consumer is not None and not consume_failed
                )
                if (
                    replay_processed
                    and hook_eid is not None
                    and subscription.replay_queue
                ):
                    subscription.replay_done.add(hook_eid)
                    _advance_replay_cursor(subscription)
                if any(isinstance(event, AgentResult) for event in events) and (
                    subscription.current_work is attribution
                ):
                    subscription.current_work = None
                    if activity is not None:
                        await self._end_session_activity(subscription, activity)
            finally:
                subscription.sink.queue.task_done()

    async def _begin_session_activity(
        self,
        subscription: TopicSubscription,
        attribution: WorkAttribution,
        screen: object,
        *,
        ready: bool | None,
        start_task: bool = True,
    ) -> SessionActivity:
        """Start the subscription-owned activity clock if it is idle."""
        activity = subscription.activity
        if activity is not None:
            return activity
        activity = SessionActivity(
            work_id=attribution.work_id,
            queue=asyncio.Queue(),
            ready=ready,
        )
        subscription.activity = activity
        consumer = self._activity_consumer
        if consumer is not None:
            await consumer(
                subscription.project_id,
                subscription.topic_id,
                activity.work_id,
                True,
            )
        if start_task:
            activity.task = asyncio.create_task(
                self._watch_session_activity(subscription, activity, screen),
                name=f"hook session activity topic={subscription.topic_id}",
            )
        return activity

    async def _end_session_activity(
        self,
        subscription: TopicSubscription,
        activity: SessionActivity,
        *,
        clear_work: bool = False,
    ) -> None:
        """Retire activity state without dropping the screen subscription."""
        if subscription.activity is not activity:
            return
        subscription.activity = None
        if clear_work:
            subscription.current_work = None
        task = activity.task
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        consumer = self._activity_consumer
        if consumer is not None:
            await consumer(
                subscription.project_id,
                subscription.topic_id,
                activity.work_id,
                False,
            )

    async def _watch_session_activity(
        self,
        subscription: TopicSubscription,
        activity: SessionActivity,
        screen: object,
    ) -> None:
        """Apply delivery, idle, liveness, and ceiling policy to the screen."""
        tracker = ActivityTracker(last_at=asyncio.get_running_loop().time())
        monitor_task = await self._channel.start_activity_monitor(screen, tracker)
        try:
            async for event in monitor_session_activity(
                queue=activity.queue,
                idle_suspect_s=self._idle_suspect_s,
                hard_ceiling_s=self._session_ceiling_s,
                unread_since=self._unread_since_for(subscription.topic_id),
                unread_grace_s=self._unread_grace_s,
                no_progress_s=self._no_progress_s,
                resume_session_id=None,
                timeout_message=self._channel.timeout_message,
                delivery_timeout_s=(
                    self._idle_suspect_s
                    if activity.ready is False
                    else self._delivery_timeout_s
                ),
                tracker=tracker,
                confirm_alive=lambda: self._channel.confirm_alive(screen),
                context=f"topic={subscription.topic_id} subscription-watch",
            ):
                if not isinstance(event, AgentResult) or not event.is_error:
                    continue
                attribution = subscription.current_work
                consumer = self._event_consumer
                if attribution is not None and consumer is not None:
                    await consumer(
                        subscription.project_id,
                        subscription.topic_id,
                        attribution.work_id,
                        event,
                        None,
                        False,
                        attribution.platform_unsolicited,
                    )
                await self._end_session_activity(
                    subscription, activity, clear_work=True
                )
        except BaseException:
            # The watch is leaving by a door it does not own. Every exit it DOES
            # own retires this activity — a result consumed upstream, an error
            # reported just above, the topic closed underneath it — so a watch
            # that ends any other way leaves it standing with nobody left to
            # take it down, and that state cannot be recovered from:
            # `_begin_session_activity` hands the NEXT turn this same dead
            # activity instead of starting one, ``current_work`` stays set so
            # the topic answers every later prompt with 「已有工作正在运行」,
            # and the room keeps its 正在思考 for the life of the process.
            #
            # Deliberately NOT in a `finally`: on the ordinary path the watch
            # can reach its end before the consumer has finished handing the
            # result to the room, and retiring here would put `turn_finished`
            # ahead of the output it belongs after — the room would stop saying
            # 正在思考 while its last message was still arriving.
            await self._end_session_activity(subscription, activity, clear_work=True)
            raise
        finally:
            if monitor_task is not None:
                monitor_task.cancel()
                try:
                    await monitor_task
                except asyncio.CancelledError:
                    pass

    async def ensure(
        self, session: SessionRef, opening: Opening, *, work_id: uuid.UUID | None = None
    ) -> tuple[object, TopicSubscription]:
        """Is this session live? Start it if not. Returns the screen and the
        subscription listening to it.

        The opening rides along on every call rather than being set once,
        because Claude Code reads a system prompt exactly once — at launch, out
        of a file this writes. An already-running session keeps the one it
        started with, so the opening matters only on the call that turns out to
        be a cold start, and no caller can know in advance which one that is.
        """
        precheck = await self._channel.precheck(session.project_id, session.topic_id)
        token = mint_scoped_token(
            project_id=str(session.project_id),
            topic_id=str(session.topic_id),
            ttl_s=SESSION_TOKEN_TTL_S,
        )
        screen = await self._channel.ensure_ready(
            project_id=session.project_id,
            topic_id=session.topic_id,
            token=token,
            env=opening.env,
            memory_scope=opening.memory_scope,
            owner=opening.owner,
            turn_id=work_id,
            launch=ClaudeLaunch(
                system_prompt=opening.system_prompt,
                model=opening.model,
                resume_session_id=opening.resume_token,
            ),
            precheck=precheck,
        )
        subscription = await self.ensure_subscription(
            session.project_id, session.topic_id
        )
        self._live[session.topic_id] = screen
        return screen, subscription

    async def send(
        self,
        session: SessionRef,
        message: str,
        opening: Opening,
        *,
        work_id: uuid.UUID,
        on_mark: Callable[[uuid.UUID], None],
        images: list[dict] | None = None,
    ) -> bool | None:
        """Put a message into the session and return once the transport has it.

        An ack, not an answer: what the agent does about this arrives through
        ``read``, possibly minutes later and possibly to a different process
        than the one that sent it.

        ``work_id`` / ``on_mark`` are the platform's turn bookkeeping riding
        along — a turn is still what the room shows and what gets billed. They
        are the part of this signature that does not belong to the contract, and
        they leave when a turn stops being how work is tracked.
        """
        topic_id = session.topic_id
        screen, subscription = await self.ensure(session, opening, work_id=work_id)
        staged, lost = await self._stage(screen, images)
        prompt = _prompt_with_native_images(message, staged, lost)
        attribution = subscription.current_work
        if attribution is None:
            attribution = WorkAttribution(
                work_id=work_id,
                queue=asyncio.Queue(),
                consumer_owned=True,
            )
            subscription.current_work = attribution
        attribution.consumer_owned = True
        on_mark(attribution.work_id)
        starts_activity = subscription.activity is None
        activity = await self._begin_session_activity(
            subscription,
            attribution,
            screen,
            ready=None,
            start_task=False,
        )
        try:
            ready = await self._channel.send_prompt(screen, prompt)
        except BaseException:
            if starts_activity:
                await self._end_session_activity(
                    subscription, activity, clear_work=True
                )
            raise
        if starts_activity:
            activity.ready = ready
            activity.task = asyncio.create_task(
                self._watch_session_activity(subscription, activity, screen),
                name=f"hook session activity topic={topic_id}",
            )
        return ready

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
                text=self._channel.needs_topic_message,
                session_id=resume_session_id,
                is_error=True,
            )
            return

        # Fail fast before screen setup: a run that cannot start must not create
        # a subscription with no live screen behind it.
        try:
            precheck = await self._channel.precheck(project_id, topic_id)
        except ScreenSetupError as exc:
            yield AgentResult(
                text=str(exc),
                session_id=resume_session_id,
                is_error=True,
                failure_code=exc.failure_code,
            )
            return

        token = mint_scoped_token(
            project_id=str(project_id),
            topic_id=str(topic_id),
            ttl_s=SESSION_TOKEN_TTL_S,
        )
        attribution: WorkAttribution | None = None
        try:
            try:
                screen = await self._channel.ensure_ready(
                    project_id=project_id,
                    topic_id=topic_id,
                    token=token,
                    env=env,
                    memory_scope=memory_scope,
                    owner=owner,
                    turn_id=turn_id,
                    launch=ClaudeLaunch(
                        system_prompt=system_prompt,
                        model=model,
                        resume_session_id=resume_session_id,
                    ),
                    precheck=precheck,
                )
                subscription = await self.ensure_subscription(project_id, topic_id)
                self._live[topic_id] = screen
                if subscription.current_work is not None:
                    raise ScreenSetupError("这个话题上已有工作正在运行，本次请求已跳过")
                attribution = WorkAttribution(
                    work_id=turn_id or uuid.uuid4(), queue=asyncio.Queue()
                )
                subscription.current_work = attribution
                staged, lost = await self._stage(screen, images)
                ready = await self._channel.send_prompt(
                    screen, _prompt_with_native_images(prompt, staged, lost)
                )
            except ScreenSetupError as exc:
                yield AgentResult(
                    text=str(exc),
                    session_id=resume_session_id,
                    is_error=True,
                    failure_code=exc.failure_code,
                )
                return
            if ready is False:
                # The driver is HOLDING the prompt until claude's input box
                # paints (a fresh screen's launcher + first boot takes over a
                # minute). Say so — the wait used to be indistinguishable from
                # a dead turn (#445).
                yield AgentMessage(
                    text=(
                        "⏳ 机器上的会话正在启动，提示词已就位，"
                        "输入框一出现就会自动发送。"
                    )
                )
            tracker = ActivityTracker(last_at=asyncio.get_event_loop().time())
            monitor_task = await self._channel.start_activity_monitor(screen, tracker)
            try:
                async for event in monitor_session_activity(
                    queue=attribution.queue,
                    idle_suspect_s=self._idle_suspect_s,
                    hard_ceiling_s=self._session_ceiling_s,
                    unread_since=self._unread_since_for(topic_id),
                    unread_grace_s=self._unread_grace_s,
                    no_progress_s=self._no_progress_s,
                    resume_session_id=resume_session_id,
                    timeout_message=self._channel.timeout_message,
                    tracker=tracker,
                    confirm_alive=lambda: self._channel.confirm_alive(screen),
                    # ready=False means the screen HOLDS the prompt until the
                    # session can take it — a queued prompt is not an undelivered
                    # one, so the 25s dead-session verdict does not apply (it
                    # misfired exactly when a wake-up summon landed while the
                    # previous turn still ran, 2026-08-16 09:21). The no-output
                    # bound keeps a genuinely dead screen from waiting forever.
                    delivery_timeout_s=(
                        self._idle_suspect_s if ready is False else DELIVERY_TIMEOUT_S
                    ),
                    context=f"topic={topic_id} turn={turn_id}",
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
            subscription = self._subscriptions.get(topic_id)
            if (
                attribution is not None
                and subscription is not None
                and subscription.current_work is attribution
            ):
                subscription.current_work = None


async def drop_topic_subscriptions(topic_id: uuid.UUID) -> None:
    """Notify every live runtime that a topic's screen was removed."""
    for runtime in list(_RUNTIMES):
        if isinstance(runtime, ClaudeCodeRuntime):
            await runtime._close_topic(topic_id)


async def drop_screen_subscriptions(screen: object) -> None:
    """Notify every live runtime that one concrete screen disappeared."""
    for runtime in list(_RUNTIMES):
        if isinstance(runtime, ClaudeCodeRuntime):
            await runtime.drop_screen_subscription(screen)


async def drop_device_subscriptions(device_id: str) -> None:
    """Notify every live runtime that a device and its topics went offline."""
    for runtime in list(_RUNTIMES):
        if isinstance(runtime, ClaudeCodeRuntime):
            await runtime.drop_device_subscriptions(device_id)
