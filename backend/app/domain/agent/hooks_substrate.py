"""Shared substrate for the hooks-driven `claude` backends (fusion-design §8.6).

Both the LOCAL backend (``TmuxHooksProvider`` — a per-topic tmux session in a
platform container) and the REMOTE backend (``DeviceProvider`` — a screen on a
user's enrolled machine over the frozen ``link.Msg`` channel) drive an
interactive ``claude`` and sense it through the same Claude Code hooks. Enrollment
and transport aside, the session flow is identical: subscribe before screen
setup, inject through the transport seam, and consume hooks continuously.

Per the fusion 统一底座 decision, the transport-INDEPENDENT parts live here so the
two backends are one substrate that can't drift — "本地/远程只差入册，不两套":

- ``hooks_settings()`` — the ``~/.claude/settings.json`` wiring Claude Code COMMAND
  hooks to the ``cheese-hook`` forwarder (HTTP hooks are blocked to non-loopback).
- ``CHEESE_HOOK_SCRIPT`` — the forwarder: POST each hook's JSON to the backend.
- ``monitor_session_activity(...)`` — shared idle, liveness, and ceiling policy.
- ``SESSION_TOKEN_TTL_S`` — the shared session-length scoped-token TTL.

All pure / transport-free, so it is unit-testable without Docker or a device.
"""

import asyncio
import logging
import uuid
import weakref
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

from app.core.sandbox_auth import mint_scoped_token
from app.domain.agent import event_spool
from app.domain.agent.hook_events import (
    HookRouter,
    HookSink,
    MessageAssembler,
    hook_router,
    translate_hook,
)
from app.domain.agent.platform_failures import (
    PROMPT_UNDELIVERED_MESSAGE,
    TURN_TIMEOUT_MESSAGE,
)
from app.domain.agent.service import (
    DISALLOWED_TOOLS,
    AgentDeliveryFailure,
    AgentEvent,
    AgentMessage,
    AgentResult,
    AgentToolUse,
)

logger = logging.getLogger(__name__)

# Screen teardown starts in transport-specific layers while subscriptions stay
# owned here. Weak references avoid keeping rebuilt pools and test providers alive.
_PROVIDERS: weakref.WeakSet[object] = weakref.WeakSet()

# How many times the session re-sends an abandoned prompt (#445) before declaring
# the screen's input path broken and letting the no-output bound take over.
_MAX_REDELIVERIES = 3

# The hook token lives with the interactive screen. Topic scope prevents a stale
# token from reaching another topic. Both transports use the same lifetime.
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
# The sentence itself lives in platform_failures, next to the classifier that
# recognises it — a copy here would drift and the failure would silently go back
# to rendering as 「AI 服务返回错误」.
UNDELIVERED_MESSAGE = PROMPT_UNDELIVERED_MESSAGE


@dataclass
class ActivityTracker:
    """Shared clock for one active period of an interactive session.

    Hook arrivals and transport-specific activity probes both touch this clock.
    It belongs to the screen subscription, independently of whichever work id
    attributes the blocks produced while the session is active.
    """

    last_at: float
    suspect_since: float | None = None

    def touch(self, at: float) -> None:
        self.last_at = at
        self.suspect_since = None


@dataclass
class HookDelivery:
    """A hook translated by the screen-lifetime consumer."""

    hook: dict
    event: AgentEvent | AgentDeliveryFailure | None
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
    prompt: str | None
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
    replay_files: dict[str, list[Path]] = field(default_factory=dict)
    replay_seen_messages: set[str] = field(default_factory=set)
    # Reassembles the screen's MessageDisplay flushes into whole messages.
    # Subscription-scoped on purpose: its dedup memory (message ids already
    # assembled) has to survive across works, or a flush redelivered after
    # its turn ended would land again as a fragment.
    assembler: MessageAssembler = field(default_factory=MessageAssembler)


HookEventConsumer = Callable[
    [
        uuid.UUID,
        uuid.UUID,
        uuid.UUID,
        AgentEvent | AgentDeliveryFailure,
        str | None,
        bool,
        bool,
    ],
    Awaitable[None],
]

HookActivityConsumer = Callable[
    [uuid.UUID, uuid.UUID, uuid.UUID, bool], Awaitable[None]
]


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
    resume_session_id: str | None,
    timeout_message: str,
    delivery_timeout_s: float = DELIVERY_TIMEOUT_S,
    delivery_message: str = UNDELIVERED_MESSAGE,
    tracker: ActivityTracker | None = None,
    confirm_alive: Callable[[], Awaitable[bool]] | None = None,
    confirm_poll_s: float = CONFIRM_POLL_S,
    on_hook: Callable[[dict], None] | None = None,
) -> AsyncIterator[AgentEvent | AgentDeliveryFailure]:
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

    With no ``tracker``/``confirm_alive`` given (the device backend today) and
    ``idle_suspect_s == hard_ceiling_s``, this reduces to exactly the old
    single-deadline behaviour.

    The subscription owns the queue lifecycle; this function only observes its
    activity stream."""
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
            delivery = await asyncio.wait_for(queue.get(), timeout=max(wait_for, 0.01))
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
        hook = delivery.hook if isinstance(delivery, HookDelivery) else delivery
        if on_hook is not None:
            on_hook(hook)
        delivered = True
        tracker.touch(now())
        event = (
            delivery.event
            if isinstance(delivery, HookDelivery)
            else translate_hook(delivery)
        )
        if event is None:
            continue
        yield event
        if isinstance(event, AgentResult):
            return  # Stop hook → session idle


class ScreenSetupError(Exception):
    """A backend couldn't bring the screen to a prompt-ready state (no Docker /
    no online device / not ready in time). Its message becomes the work error
    result — the ONE place setup failures turn into an ``AgentResult``."""


def _prompt_with_native_images(prompt: str, images: list[dict] | None) -> str:
    """Use Claude Code's own @path attachment path for interactive sessions."""
    paths = list(
        dict.fromkeys(
            str(image.get("path") or "").strip()
            for image in images or []
            if str(image.get("path") or "").strip()
        )
    )
    if not paths:
        return prompt
    mentions = "\n".join(f"@{path}" for path in paths)
    return f"{prompt}\n\n{mentions}" if prompt else mentions


class HooksSessionProvider[ScreenT]:
    """Base for the hooks-driven backends (fusion-design §8.6, increment 2).

    Owns the transport-independent subscription and activity lifecycle so local
    and remote screens cannot drift. Subclasses only implement screen setup,
    prompt injection, and transport liveness.

    A subclass ("本地/远程只差 transport/入册") implements only the transport seam:
    ``_precheck`` (cheap fail-fast BEFORE the queue is claimed), ``_ensure_ready``
    (→ a screen ctx of type ``ScreenT``) and ``_send_prompt``, plus the class-level
    ``name`` / ``_needs_topic_message`` / ``_timeout_message``. All three raise
    ``ScreenSetupError`` to surface a clean error result. This is the strategy
    behind ``TmuxHooksProvider`` (local docker/tmux) and ``DeviceProvider``
    (remote link.Msg)."""

    name: str = "hooks"
    # Claude Code resolves an @-mentioned local image into the same native image
    # block its clipboard paste path produces. Remote devices stage the bytes
    # before this text is sent; local screens already share the worktree.
    embeds_images = True
    _needs_topic_message = "本轮需要话题上下文"
    # A subclass may prefix its transport ("tmux …" / "device …") but MUST keep
    # TURN_TIMEOUT_MARKER in the string — that attribution is how the failure gets
    # classified as a timeout rather than an AI-service error.
    _timeout_message = TURN_TIMEOUT_MESSAGE

    def __init__(
        self,
        *,
        router: HookRouter | None = None,
        idle_suspect_s: float = 900.0,
        hard_ceiling_s: float = 900.0,
        delivery_timeout_s: float = DELIVERY_TIMEOUT_S,
    ) -> None:
        self._router = router or hook_router
        # Equal by default preserves the legacy single-deadline behavior.
        self._idle_suspect_s = idle_suspect_s
        self._hard_ceiling_s = hard_ceiling_s
        self._delivery_timeout_s = delivery_timeout_s
        # Screen-lifetime state. ``_live`` is the transport handle; subscriptions
        # own the stable router sink, consumer task, and current attribution.
        self._live: dict[uuid.UUID, ScreenT] = {}
        self._subscriptions: dict[uuid.UUID, TopicSubscription] = {}
        self._event_consumer: HookEventConsumer | None = None
        self._activity_consumer: HookActivityConsumer | None = None
        # A transport write is not delivery. One waiter per topic tracks the
        # exact UserPromptSubmit hook proving Claude Code accepted a mid-turn
        # message into its own input queue.
        self._delivery_locks: dict[uuid.UUID, asyncio.Lock] = {}
        self._delivery_receipts: dict[uuid.UUID, tuple[str, asyncio.Future[bool]]] = {}
        _PROVIDERS.add(self)

    @property
    def hard_ceiling_s(self) -> float:
        """This provider's absolute active-session ceiling."""
        return self._hard_ceiling_s

    def available(self) -> bool:
        return True

    def bind_event_consumer(self, consumer: HookEventConsumer) -> None:
        """Bind the room persistence and broadcast callback owned by ChatService."""
        self._event_consumer = consumer

    def bind_activity_consumer(self, consumer: HookActivityConsumer) -> None:
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
                return False
            delivered_text = _prompt_with_native_images(text, images)
            receipt: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
            self._delivery_receipts[topic_id] = (delivered_text, receipt)
            try:
                if images:
                    await self._send_prompt(screen, delivered_text, images=images)
                else:
                    await self._send_prompt(screen, delivered_text)
                try:
                    return await asyncio.wait_for(receipt, timeout=DELIVERY_TIMEOUT_S)
                except TimeoutError:
                    logger.warning(
                        "mid-turn delivery receipt timed out (topic=%s)", topic_id
                    )
                    return False
            except ScreenSetupError:
                return False
            except Exception:  # noqa: BLE001 — failed inject falls back to a turn
                logger.exception(
                    "deliver into running turn failed (topic=%s)", topic_id
                )
                return False
            finally:
                pending = self._delivery_receipts.get(topic_id)
                if pending is not None and pending[1] is receipt:
                    self._delivery_receipts.pop(topic_id, None)

    def _observe_delivery_hook(self, topic_id: uuid.UUID, hook: dict) -> None:
        pending = self._delivery_receipts.get(topic_id)
        if pending is None:
            return
        expected, receipt = pending
        event_name = str(hook.get("hook_event_name") or hook.get("hookEventName") or "")
        if (
            event_name == "UserPromptSubmit"
            and hook.get("prompt") == expected
            and not receipt.done()
        ):
            receipt.set_result(True)

    def _finish_delivery_wait(self, topic_id: uuid.UUID) -> None:
        pending = self._delivery_receipts.pop(topic_id, None)
        if pending is not None and not pending[1].done():
            pending[1].set_result(False)

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

    async def recover_subscriptions(
        self, device_id: str | None = None
    ) -> list[TopicSubscription]:
        """Discover surviving transport screens after a backend restart."""
        del device_id
        return []

    async def drop_device_subscriptions(self, device_id: str) -> None:
        """Drop recovered subscriptions associated with a disconnected device."""
        del device_id

    async def drop_subscription(self, topic_id: uuid.UUID) -> None:
        """Drop the sink and consumer after the topic's screen is known dead."""
        subscription = self._subscriptions.pop(topic_id, None)
        self._live.pop(topic_id, None)
        self._finish_delivery_wait(topic_id)
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
            await self.drop_subscription(topic_id)

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
                activity = subscription.activity
                if activity is None and (
                    attribution.platform_unsolicited or attribution.consumer_owned
                ):
                    screen = self._live.get(subscription.topic_id)
                    if screen is not None:
                        activity = await self._begin_session_activity(
                            subscription,
                            attribution,
                            screen,
                            prompt=None,
                            ready=True,
                        )
                eid_value = hook.get("_eid")
                hook_eid = eid_value if isinstance(eid_value, str) else None
                # One hook can surface zero events (a MessageDisplay flush
                # still buffering toward its message) or several (a Stop
                # draining a partial message ahead of the result); each
                # surfaced event routes exactly like the old one-hook-one-event
                # flow did.
                events = subscription.assembler.translate(hook)
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
                replay_processed = (not events) or (
                    consumer_owned and consumer is not None and not consume_failed
                )
                if replay_processed and hook_eid is not None:
                    event_spool.remove(subscription.replay_files.pop(hook_eid, []))
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
        screen: ScreenT,
        *,
        prompt: str | None,
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
            prompt=prompt,
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
        screen: ScreenT,
    ) -> None:
        """Apply delivery, idle, liveness, and ceiling policy to the screen."""
        tracker = ActivityTracker(last_at=asyncio.get_running_loop().time())
        monitor_task = await self._start_activity_monitor(screen, tracker)
        redeliveries = 0
        try:
            async for event in monitor_session_activity(
                queue=activity.queue,
                idle_suspect_s=self._idle_suspect_s,
                hard_ceiling_s=self._hard_ceiling_s,
                resume_session_id=None,
                timeout_message=self._timeout_message,
                delivery_timeout_s=(
                    self._idle_suspect_s
                    if activity.ready is False
                    else self._delivery_timeout_s
                ),
                tracker=tracker,
                confirm_alive=lambda: self._confirm_alive(screen),
            ):
                if isinstance(event, AgentDeliveryFailure):
                    if activity.prompt is not None:
                        redeliveries += 1
                        if redeliveries <= _MAX_REDELIVERIES:
                            await self._send_prompt(screen, activity.prompt)
                    continue
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
        finally:
            if monitor_task is not None:
                monitor_task.cancel()
                try:
                    await monitor_task
                except asyncio.CancelledError:
                    pass

    async def inject_work(
        self,
        *,
        project_id: uuid.UUID,
        topic_id: uuid.UUID,
        prompt: str,
        system_prompt: str,
        resume_session_id: str | None,
        work_id: uuid.UUID,
        on_mark: Callable[[uuid.UUID], None],
        model: str | None = None,
        env: dict[str, str] | None = None,
        memory_scope: str | None = None,
        owner: str | None = None,
        sandbox_image: str | None = None,
        images: list[dict] | None = None,
    ) -> bool | None:
        """Inject work into the live session and return after transport receipt."""
        del sandbox_image
        prompt = _prompt_with_native_images(prompt, images)
        precheck = await self._precheck(project_id, topic_id)
        token = mint_scoped_token(
            project_id=str(project_id),
            topic_id=str(topic_id),
            ttl_s=SESSION_TOKEN_TTL_S,
        )
        screen = await self._ensure_ready(
            project_id=project_id,
            topic_id=topic_id,
            token=token,
            model=model,
            env=env,
            memory_scope=memory_scope,
            owner=owner,
            turn_id=work_id,
            resume_session_id=resume_session_id,
            system_prompt=system_prompt,
            precheck=precheck,
        )
        subscription = await self.ensure_subscription(project_id, topic_id)
        self._live[topic_id] = screen
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
            prompt=prompt,
            ready=None,
            start_task=False,
        )
        try:
            if images:
                ready = await self._send_prompt(screen, prompt, images=images)
            else:
                ready = await self._send_prompt(screen, prompt)
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

    async def _send_prompt(
        self, screen: ScreenT, prompt: str, images: list[dict] | None = None
    ) -> bool | None:
        """Deliver the turn's prompt to the ready screen. Transport-specific.

        Returns the driver's readiness at delivery time when the transport can
        know it (the device cheeselet answers ``{ready: bool}``): ``False``
        means the prompt is HELD until the input box paints — worth a visible
        line in the room instead of silence (#445). ``None`` = unknown."""
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

        prompt = _prompt_with_native_images(prompt, images)

        # Fail fast before screen setup: a run that cannot start must not create
        # a subscription with no live screen behind it.
        try:
            precheck = await self._precheck(project_id, topic_id)
        except ScreenSetupError as exc:
            yield AgentResult(
                text=str(exc), session_id=resume_session_id, is_error=True
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
                subscription = await self.ensure_subscription(project_id, topic_id)
                self._live[topic_id] = screen
                if subscription.current_work is not None:
                    raise ScreenSetupError("这个话题上已有工作正在运行，本次请求已跳过")
                attribution = WorkAttribution(
                    work_id=turn_id or uuid.uuid4(), queue=asyncio.Queue()
                )
                subscription.current_work = attribution
                if images:
                    ready = await self._send_prompt(screen, prompt, images=images)
                else:
                    ready = await self._send_prompt(screen, prompt)
            except ScreenSetupError as exc:
                yield AgentResult(
                    text=str(exc), session_id=resume_session_id, is_error=True
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
            monitor_task = await self._start_activity_monitor(screen, tracker)
            try:
                # Bounded so a screen whose terminal genuinely eats every write
                # cannot ping-pong forever: past the cap the failure stays
                # visible and the turn falls to the no-output bound as before.
                redeliveries = 0
                async for event in monitor_session_activity(
                    queue=attribution.queue,
                    idle_suspect_s=self._idle_suspect_s,
                    hard_ceiling_s=self._hard_ceiling_s,
                    resume_session_id=resume_session_id,
                    timeout_message=self._timeout_message,
                    tracker=tracker,
                    confirm_alive=lambda: self._confirm_alive(screen),
                    # ready=False means the driver HOLDS the prompt until the
                    # input box paints — a queued prompt is not an undelivered
                    # one, so the 25s dead-session verdict does not apply (it
                    # misfired exactly when a wake-up summon landed while the
                    # previous turn still ran, 2026-08-16 09:21). The driver's
                    # own give-up (#445 deliveryFailed) and the no-output bound
                    # keep a genuinely dead screen from waiting forever.
                    delivery_timeout_s=(
                        self._idle_suspect_s if ready is False else DELIVERY_TIMEOUT_S
                    ),
                ):
                    if isinstance(event, AgentDeliveryFailure):
                        # The driver gave up (#445) — re-send NOW instead of
                        # letting the room wait out the 300s bound. The event
                        # itself never reaches the chat layer.
                        redeliveries += 1
                        if redeliveries <= _MAX_REDELIVERIES:
                            yield AgentMessage(
                                text=(
                                    "⚠️ 提示词没能送进机器上的会话"
                                    f"（{event.phase} 阶段，{event.ticks} 次尝试）"
                                    "，正在自动重投…"
                                )
                            )
                            try:
                                await self._send_prompt(screen, prompt)
                            except ScreenSetupError as exc:
                                yield AgentResult(
                                    text=str(exc),
                                    session_id=resume_session_id,
                                    is_error=True,
                                )
                                return
                        else:
                            yield AgentMessage(
                                text=(
                                    "⚠️ 提示词多次重投仍未送达——这台机器的"
                                    "终端链路有问题，本轮将按超时处理。"
                                )
                            )
                        continue
                    yield event
            finally:
                if monitor_task is not None:
                    monitor_task.cancel()
                    try:
                        await monitor_task
                    except asyncio.CancelledError:
                        pass
        finally:
            self._finish_delivery_wait(topic_id)
            subscription = self._subscriptions.get(topic_id)
            if (
                attribution is not None
                and subscription is not None
                and subscription.current_work is attribution
            ):
                subscription.current_work = None


async def drop_topic_subscriptions(topic_id: uuid.UUID) -> None:
    """Notify every live hooks provider that a topic's screen was removed."""
    for provider in list(_PROVIDERS):
        if isinstance(provider, HooksSessionProvider):
            await provider.drop_subscription(topic_id)


async def drop_screen_subscriptions(screen: object) -> None:
    """Notify providers that one concrete transport screen disappeared."""
    for provider in list(_PROVIDERS):
        if isinstance(provider, HooksSessionProvider):
            await provider.drop_screen_subscription(screen)


async def drop_device_subscriptions(device_id: str) -> None:
    """Notify providers that one device and its recovered topics went offline."""
    for provider in list(_PROVIDERS):
        if isinstance(provider, HooksSessionProvider):
            await provider.drop_device_subscriptions(device_id)


def schedule_topic_subscription_drop(topic_id: uuid.UUID) -> bool:
    """Bridge synchronous workspace teardown into async subscription cleanup."""
    from app.core.background import spawn

    return spawn(
        drop_topic_subscriptions(topic_id),
        name=f"drop hook subscription topic={topic_id}",
    )


def schedule_screen_subscription_drop(screen: object) -> bool:
    """Bridge synchronous container teardown into async subscription cleanup."""
    from app.core.background import spawn

    return spawn(
        drop_screen_subscriptions(screen),
        name=f"drop hook subscription screen={screen}",
    )
