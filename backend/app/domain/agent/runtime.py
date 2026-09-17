"""AgentWorkRunner + Broker: run agent work as background jobs.

WebSocket connections subscribe and relay; they never own model work. A
disconnect drops only the subscriber while the background request or live
session continues and persists independently.

Today's Broker is in-process (single backend instance). Multi-instance needs a
cross-process broker (Valkey/PG) + a durable per-topic lease + a per-turn replay
buffer/cursor for seamless mid-turn reconnect — see design v2 R1/R3. The current
single-writer guarantee is ChatService's per-topic asyncio lock (process-local);
the durable lease is the documented multi-instance upgrade.
"""

import asyncio
import contextlib
import logging
import time
import uuid
from collections import deque
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime
from functools import lru_cache

from app.core.background import hold
from app.core.errors import AppError
from app.core.obs import bind_context, clear_context
from app.domain.agent.host_failure import handle_host_failure, record_host_success
from app.domain.agent.platform_failures import (
    HOST_SCOPED_CODES,
    SUBSCRIPTION_CREDENTIAL_EXPIRED,
    classify_platform_failure,
)
from app.domain.agent.platform_notices import (
    EVENT_DEPLOY_INTERRUPTED,
    EVENT_TOOLS_RECOVERED,
    EVENT_TURN_FAILED,
    EVENT_TURN_QUEUED,
    EVENT_TURN_TIMEOUT,
    SEVERITY_ERROR,
    SEVERITY_INFO,
    SEVERITY_WARN,
    WHO_HUMAN,
    WHO_PLATFORM,
    delivery_fallback_notice,
    notice,
)
from app.domain.agent.repositories import AgentTurnRepository, TurnRecord
from app.domain.identity.actor import Actor
from app.domain.identity.handles import names_a_person

logger = logging.getLogger("cheesex.runtime")


def _fire_on_done(callback: Callable[[], None]) -> None:
    """Run a `submit(on_done=...)` hook without letting it escape into the loop.

    A done-callback that raises does not fail the turn (that already finished) —
    it lands in the loop's exception handler as an unattributed error. Swallow
    and log instead, so a bookkeeping bug in a caller stays a bookkeeping bug.
    """
    try:
        callback()
    except Exception:  # noqa: BLE001 — a hook must never break the runner
        logger.exception("submit on_done hook failed")


async def _open_turns(session_factory) -> dict[uuid.UUID, TurnRecord]:
    """Every turn interval still open, keyed by turn id.

    Whether reading this can fail is the sweep's business, not this function's:
    it raises, and the callers that must survive a database blip say so where
    they say what else they do on failure.
    """
    async with session_factory() as session:
        rows = await AgentTurnRepository(session).open_turns()
    return {record.turn_id: record for record in rows}


async def _open_turn(session_factory, **fields) -> None:
    async with session_factory() as session:
        await AgentTurnRepository(session).open(**fields)
        await session.commit()


async def _stamp_delivery(session_factory, turn_id: uuid.UUID) -> None:
    """Record that the transport accepted this turn's prompt.

    Swallows its own failure, unlike opening the interval. This runs mid-turn on
    a turn that is working: losing the stamp costs at most one duplicate re-send
    if the process then dies, while raising here would kill the live turn to
    protect it from a hypothetical one — a trade nobody would make deliberately.
    """
    try:
        async with session_factory() as session:
            await AgentTurnRepository(session).mark_delivered(turn_id, _utcnow())
            await session.commit()
    except Exception:  # noqa: BLE001 — bookkeeping must not kill a working turn
        logger.exception("could not stamp delivery for turn %s", turn_id)


async def _close_turns(session_factory, turn_ids) -> None:
    async with session_factory() as session:
        await AgentTurnRepository(session).close(turn_ids, _utcnow())
        await session.commit()


def _utcnow() -> datetime:
    return datetime.now(UTC)


# Channel = the topic id (str). Frames are the same dicts converse yields.
Frame = dict


class InProcessBroker:
    """Fan-out pub/sub for one process, with a per-channel replay buffer of the
    IN-PROGRESS turn's ephemeral frames (R3). A connection that subscribes mid-turn
    gets those frames immediately (catch-up), then the live continuation — so a
    reconnect (after `GET /blocks` for persisted history) is seamless.

    Active work is keyed by its compatibility id. A message folded into a live
    Claude session emits no synthetic completion boundary; only the session's
    existing lifecycle markers own active state.
    """

    def __init__(self, replay_size: int = 512) -> None:
        self._subs: dict[str, set[asyncio.Queue[Frame]]] = {}
        self._buffer: dict[str, list[Frame]] = {}
        self._active: dict[str, set[str]] = {}
        self._active_since: dict[tuple[str, str], float] = {}
        self._last_activity_at: dict[str, float] = {}
        self._replay_size = replay_size
        self._message_subscriber: Callable[..., None] | None = None

    def reset(self) -> None:
        """Drop all buffered frames + subscriptions. The broker is a process-wide
        singleton (get_broker is lru_cached); tests that TRUNCATE ... RESTART
        IDENTITY reuse channel ids (topic id 1, 2, …) across tests, so without this
        a prior test's buffered frames would replay into the next test on the same
        reused channel. Called between tests by the client/python_client fixtures."""
        self._subs.clear()
        self._buffer.clear()
        self._active.clear()
        self._active_since.clear()
        self._last_activity_at.clear()

    async def receive_message(
        self,
        chat_service,
        topic_id: uuid.UUID,
        *,
        author: str,
        content: str,
        summon: bool,
        reply_to: str | None = None,
        attachments: list[dict] | None = None,
        provision_actor: Actor | None = None,
        client_id: str | None = None,
    ) -> uuid.UUID:
        """Persist one human message now, then schedule AI work if requested.

        Receiving a message is free collaboration state; running a model turn is
        metered work. Keeping those as two operations makes the ordering real:
        the project queue and credit gate can delay/refuse only the latter.
        """
        received_at = time.monotonic()
        channel = str(topic_id)
        # Capture the user's arrival-time expectation before the database write.
        # The live session may finish while the message is being persisted; that
        # race is still a delivery fallback, not an ordinary idle-topic message.
        live_delivery_expected = chat_service.has_running_turn(topic_id) or bool(
            self.active_turn_ids(channel)
        )
        (
            payloads,
            user_block_id,
            user_block_ids,
            duplicate,
        ) = await chat_service.post_user_message(
            topic_id,
            author=author,
            content=content,
            turn_id=None,
            reply_to=reply_to,
            attachments=attachments,
            client_id=client_id,
        )
        turn_id = user_block_id
        recipient_handle = next(
            (
                (payload.get("meta") or {}).get("agent_recipient", {}).get("handle")
                for payload in payloads
                if (payload.get("meta") or {}).get("agent_recipient")
            ),
            None,
        )
        summon = summon or any(
            (payload.get("meta") or {}).get("agent_recipient", {}).get("mentioned")
            for payload in payloads
        )
        persisted_at = time.monotonic()
        for payload in payloads:
            await self.publish(channel, {"type": "user_block", "block": payload})
        logger.info(
            "chat_receive_timing topic=%s turn=%s persist_ms=%.3f publish_ms=%.3f",
            topic_id,
            turn_id,
            (persisted_at - received_at) * 1000,
            (time.monotonic() - persisted_at) * 1000,
        )
        if duplicate:
            return turn_id
        if self._message_subscriber is not None:
            self._message_subscriber(
                chat_service,
                topic_id,
                turn_id,
                summon=summon,
                continuation_id=turn_id,
                author=author,
                content=next(
                    (
                        payload["content"]
                        for payload in payloads
                        if payload.get("id") == str(user_block_id) and content
                    ),
                    content,
                ),
                reply_to=reply_to,
                attachments=attachments,
                provision_actor=provision_actor,
                landed_user_block_id=user_block_id,
                landed_user_block_ids=user_block_ids,
                live_delivery_expected=live_delivery_expected,
                recipient_handle=recipient_handle,
            )
        return turn_id

    def subscribe_messages(self, subscriber: Callable[..., None]) -> None:
        """Subscribe to accepted sends; browser replay never enters this stream."""
        self._message_subscriber = subscriber

    async def publish(self, channel: str, frame: Frame) -> None:
        kind = frame.get("type")
        # Reaction frames are standalone state updates, not turn progress: they
        # can fire on an idle channel (a human reacting between turns) and are
        # rebuilt from GET /blocks on (re)connect — so they are fanned out live
        # but never buffered (buffering would also make an idle channel look
        # in_flight forever).
        if kind == "reaction":
            for q in list(self._subs.get(channel, ())):
                q.put_nowait(frame)
            return

        if kind == "turn_started":
            turn_id = str(frame.get("turn_id") or "")
            if turn_id:
                self._active.setdefault(channel, set()).add(turn_id)
                self._active_since.setdefault((channel, turn_id), time.time())

        if self._active.get(channel):
            self._last_activity_at[channel] = time.monotonic()

        # Idle state changes and persisted blocks are fanned out live but never
        # retained. This is what stops a queue notice or other system event from
        # making a reconnect look like an agent turn is still running.
        if self._active.get(channel):
            buf = self._buffer.setdefault(channel, [])
            buf.append(frame)
            if len(buf) > self._replay_size:
                del buf[: len(buf) - self._replay_size]

        if kind == "turn_finished":
            turn_id = str(frame.get("turn_id") or "")
            active = self._active.get(channel)
            if active is not None:
                active.discard(turn_id)
                self._active_since.pop((channel, turn_id), None)
                if not active:
                    self._active.pop(channel, None)
                    self._buffer.pop(channel, None)
                    self._last_activity_at.pop(channel, None)

        for q in list(self._subs.get(channel, ())):
            q.put_nowait(frame)

    def in_flight(self, channel: str) -> bool:
        """True while at least one explicitly-started turn is active. Lets a
        (re)connecting client rebuild the 正在思考 indicator instead of showing
        a silent, seemingly-dead topic."""
        return bool(self._active.get(channel))

    def active_turn_ids(self, channel: str) -> list[str]:
        """Stable snapshot for a reconnecting client."""
        return sorted(self._active.get(channel, ()))

    def active_channels(self) -> set[str]:
        """Channels whose session or request activity is currently live."""
        return set(self._active)

    def active_count(self) -> int:
        """Number of live attributed work ids across all channels."""
        return sum(len(ids) for ids in self._active.values())

    def activity_snapshot(self, channel: str) -> dict | None:
        """Current attribution and activity time for one channel."""
        ids = self.active_turn_ids(channel)
        if not ids:
            return None
        newest = max(ids, key=lambda work_id: self._active_since[(channel, work_id)])
        last_at = self._last_activity_at.get(channel)
        return {
            "turn_id": newest,
            "started_at": self._active_since[(channel, newest)],
            "idle_for_s": (time.monotonic() - last_at if last_at is not None else None),
        }

    @contextlib.asynccontextmanager
    async def subscribe(
        self, channel: str, *, replay: bool = False
    ) -> AsyncIterator[asyncio.Queue[Frame]]:
        q: asyncio.Queue[Frame] = asyncio.Queue()
        if replay:
            for frame in self._buffer.get(channel, ()):
                q.put_nowait(frame)
        self._subs.setdefault(channel, set()).add(q)
        try:
            yield q
        finally:
            subs = self._subs.get(channel)
            if subs is not None:
                subs.discard(q)
                if not subs:
                    self._subs.pop(channel, None)


@lru_cache
def get_broker() -> InProcessBroker:
    """Process-wide singleton — defined here (not app.api.deps) so domain code
    that needs to publish outside a request/route (background watchers, retry
    loops) can reach the SAME broker instance without importing the api layer."""
    return InProcessBroker()


class AgentWorkRunner:
    """Admit background work and publish its frames to the broker.

    Interactive session lifecycle is owned by its provider subscription. This
    runner owns only request admission, non-interactive request execution, and
    recovery bookkeeping. It is process-scoped so work survives subscribers.
    """

    def __init__(
        self,
        broker: InProcessBroker,
        *,
        turn_timeout_s: float = 900.0,
        first_output_timeout_s: float = 300.0,
        credential_expiry_of: Callable[[uuid.UUID], int | None] | None = None,
        credential_expired_fuse_s: float = 15.0,
    ) -> None:
        self._broker = broker
        self._message_locks: dict[tuple[uuid.UUID, str | None], asyncio.Lock] = {}
        self._timeout = turn_timeout_s
        # 冷启动看门狗: how long a turn may produce NOTHING before it is called
        # dead. Separate from `turn_timeout_s` because it answers a different
        # question — that one asks "is this turn taking too long?", this one asks
        # "did this turn ever start?". 0 disables it. See `_execute`.
        self._first_output_timeout_s = first_output_timeout_s
        # #388 缺陷一: a lookup for "the model credential this topic's turn will run
        # with is already expired". When it answers yes, the backend KNOWS the turn
        # is doomed (the metering proxy / upstream rejects every request), so the
        # cold-start fuse is cut to `credential_expired_fuse_s` — a short grace that
        # still lets a credential refreshed between setup and now speak first (which
        # retires the fuse) — and the failure event says the TRUE reason instead of
        # guessing container/disk/network. `None` (the default, and every backend
        # with no such signal) leaves the fuse byte-for-byte unchanged.
        self._credential_expiry_of = credential_expiry_of
        self._credential_expired_fuse_s = credential_expired_fuse_s
        # Keep references so tasks aren't GC'd mid-flight (and for shutdown).
        self._tasks: set[asyncio.Task] = set()
        # 可 debug: lifecycle summaries of the last ~100 turns (/debug/turns).
        self._recent: deque[dict] = deque(maxlen=100)
        # Project-level concurrency gate (spec §9.1): at most N turns run at
        # once per project; excess turns queue on the semaphore (FIFO). The
        # queue is asyncio-only — a restart drops it, which is accepted; the
        # queued state is visible as a system event in the topic.
        self._project_sems: dict[str, asyncio.Semaphore] = {}
        self._project_waiting: dict[str, int] = {}
        # Turn ids THIS process is actually executing right now → the task
        # running them. The durable registry on disk cannot answer that question
        # — it records every turn that ever started and was not cleaned up,
        # whether by this generation of the process or a dead one.
        #
        # It holds the TASK, not just the id, because "not running it" is only
        # half the orphan set: a turn can also be in here and wedged (the child
        # container died, the stream never ends). Claiming one of those means
        # cancelling it — a resume would otherwise queue behind the zombie on
        # ChatService's per-topic lock and never run. See sweep_orphans.
        self._live: dict[str, asyncio.Task] = {}
        # Monotonic timestamp of the last frame each live turn published. Frames
        # include tool calls, which persist no Block — so this sees activity the
        # DB cannot, and keeps a long tool-only stretch from looking dead.
        self._last_frame_at: dict[str, float] = {}
        # Which topic each live turn belongs to. Kept in memory rather than read
        # back off the on-disk registry because `live_work_for_topic` answers a
        # request (`/topics/{id}/status`), and that must not cost a file read.
        self._live_topics: dict[str, uuid.UUID] = {}
        # Topics whose last turn died of a host-scoped failure (#186). Clearing the
        # machine's failure streak costs a DB round-trip, and a turn must not wait
        # on bookkeeping to be released — `_live` is emptied only after `_execute`
        # returns, and `live_work_for_topic` is the heartbeat half of the stall
        # verdict, so a slow tail here reads as "still running" to every caller.
        # Remembering who actually failed keeps the happy path free of it entirely;
        # what this set cannot see (a failure recorded before a restart) is covered
        # by the staleness rule in `device.health` instead.
        self._host_failed_topics: set[str] = set()

    def recent_work(self) -> list[dict]:
        """Newest-first lifecycle summaries for /debug/turns."""
        return list(reversed(self._recent))

    def active_work_count(self) -> int:
        """How many turns are currently in flight — /health exposes this so a
        redeploy can drain (wait for running turns) instead of killing them."""
        return max(len(self._tasks), self._broker.active_count())

    async def drain(self, timeout_s: float = 5.0) -> None:
        """Wait for every task this runner still has in flight — a turn, a
        follow-up wake — instead of a caller guessing how long the tail takes.

        A turn's own coroutine keeps running after it has published its last
        frame (settling conclusion cards, closing the interval; see the tail of
        `_execute`), so a test that only waits for that frame and then returns
        races it: the per-test event loop closes under the still-running task,
        which freezes it mid-transaction holding a DB lock the next test's
        TRUNCATE then waits on. Awaiting this instead is what a test ends on.

        Best-effort: on timeout this just stops waiting rather than raising —
        it is on the caller to decide what that means (a test's own teardown
        check is what turns a task still pending here into a named failure).
        """
        pending = {t for t in self._tasks if not t.done()}
        if not pending:
            return
        await asyncio.wait(pending, timeout=timeout_s)

    def topic_work(self, topic_id: uuid.UUID) -> dict | None:
        """Latest lifecycle record for this topic. `ceiling_s` is this turn's
        effective absolute ceiling (`self._timeout`, or the channel's own hard
        ceiling once its `turn_ceiling` frame has rescheduled the outer wrap —
        see `_execute`) and `near_ceiling` is a coarse "within the last 10
        minutes" flag — turn 活跃度检测 deliberately does NOT expose a live
        `budget_left_s` countdown any more: that figure was observed making the
        agent rush against what's only meant to be a wedged-turn safety net
        (dev, 2026-08-08).
        Ring-buffer-backed, so None after a restart or ~100 turns elsewhere."""
        key = str(topic_id)
        activity = self._broker.activity_snapshot(key)
        if activity is not None:
            for rec in reversed(self._recent):
                if rec.get("turn_id") != activity["turn_id"]:
                    continue
                out = dict(rec)
                out["status"] = "running"
                out["started_at"] = activity["started_at"]
                out["ceiling_s"] = round(rec.get("ceiling_s") or self._timeout)
                out["near_ceiling"] = False
                return out
            return {
                "turn_id": activity["turn_id"],
                "topic_id": key,
                "status": "running",
                "started_at": activity["started_at"],
                "ceiling_s": round(self._timeout),
                "near_ceiling": False,
            }
        for rec in reversed(self._recent):
            if rec["topic_id"] != key:
                continue
            out = dict(rec)
            ceiling_s = rec.get("ceiling_s") or self._timeout
            out["ceiling_s"] = round(ceiling_s)
            if rec["status"] == "running":
                elapsed = time.time() - rec["started_at"]
                out["near_ceiling"] = (ceiling_s - elapsed) < 600
            return out
        return None

    def continuation_for(self, topic_id: uuid.UUID) -> uuid.UUID | None:
        """The logical unit of work this topic's CURRENT turn belongs to, or
        None when no turn of ours is running.

        This is how an HTTP handler — which is called by the sandbox over a
        plain request and knows nothing about turns — finds the key namespace to
        dedup against. None means "not inside an automatic turn": a human
        clicking a button twice means it twice, so the caller skips the check
        rather than inventing a namespace."""
        rec = self._current_turn_record(topic_id)
        raw = rec.get("continuation_id") if rec is not None else None
        return uuid.UUID(raw) if isinstance(raw, str) else None

    def turn_author_for(self, topic_id: uuid.UUID) -> str | None:
        """The HUMAN whose turn is running on this topic right now — who is
        actually driving the work — or None when nobody identifiable is.

        The answer a sandbox-side action cannot supply for itself: 芝士 calls
        `cheese split` under her own `cheese-<hex12>` handle, so the endpoint sees
        the robot and not the person who asked. That person is right here in the
        turn record, next to the continuation id the split endpoint already reads.

        None covers three cases the caller must treat identically — fall back to
        whatever it did before: no turn of ours is running; the turn was started
        by the platform itself (`author="system"` — gate verdicts, scheduled
        wake-ups, conflict nudges); or it was started by
        a 分身 working autonomously. Only a real person's handle comes back."""
        rec = self._current_turn_record(topic_id)
        author = rec.get("author") if rec is not None else None
        if not isinstance(author, str) or not names_a_person(author):
            return None
        return author

    def note_session_output(self, turn_id: uuid.UUID, *, tool: bool) -> None:
        """A live session produced something for this turn.

        The turn summary (`/debug/turns`) is stamped from frames crossing the
        request's own stream, and a session's output does not cross it — the
        call that started the turn returned before the agent said anything. So
        every turn read back as `first_output_s: null` / `tools: 0`, which is
        the exact signature of a sandbox whose hooks never arrive: the one
        failure the summary exists to make visible was indistinguishable from
        every healthy turn.
        """
        for rec in reversed(self._recent):
            if rec.get("turn_id") != str(turn_id):
                continue
            if tool:
                rec["tools"] = int(rec.get("tools") or 0) + 1
            if rec.get("first_output_s") is None and rec.get("started_at"):
                rec["first_output_s"] = round(time.time() - rec["started_at"], 2)
            self._last_frame_at[str(turn_id)] = time.monotonic()
            return

    def _current_turn_record(self, topic_id: uuid.UUID) -> dict | None:
        """The `_recent` entry for the turn this topic is running NOW, or None.

        Shared by `continuation_for` and `turn_author_for` so the two cannot
        disagree about which turn "now" means. `_recent` is a ring buffer of what
        turns *did*, so the newest entry for a topic is not necessarily live —
        hence the two guards: prefer the broker's own live turn id, and when the
        broker has none, accept the newest entry only while it still reads
        `running`."""
        key = str(topic_id)
        activity = self._broker.activity_snapshot(key)
        active_id = activity["turn_id"] if activity is not None else None
        for rec in reversed(self._recent):
            if rec["topic_id"] != key:
                continue
            if active_id is not None and rec.get("turn_id") != active_id:
                continue
            if active_id is None and rec["status"] != "running":
                return None
            return rec
        return None

    def running_topic_ids(self) -> set[uuid.UUID]:
        """Every topic with a turn currently in flight — for bulk UI signals
        (e.g. the sidebar's "还在说话" indicator) that can't afford one
        `topic_work()` lookup per row. Same "newest record per topic wins"
        rule as `topic_work()`, just collected across all topics at once."""
        seen: set[str] = set()
        running: set[uuid.UUID] = set()
        for channel in self._broker.active_channels():
            try:
                running.add(uuid.UUID(channel))
            except ValueError:
                continue
        for rec in reversed(self._recent):
            key = rec["topic_id"]
            if key in seen:
                continue
            seen.add(key)
            if rec["status"] == "running":
                running.add(uuid.UUID(key))
        return running

    def live_work_for_topic(self, topic_id: uuid.UUID) -> dict | None:
        """The turn THIS process is actually executing for `topic_id`, with how
        long since it last published a frame — or None if nobody is running one.

        This is the heartbeat half of the stall verdict (see
        `TopicService.stall_signal`), and deliberately not `topic_work()`:
        `_recent` is a ring buffer of what turns *did*, so a turn killed with the
        process still reads `running` there forever. `_live` is emptied by the
        turn's own `finally`, which a dying process never gets to run — so a
        registry entry with no `_live` entry means the executor is gone, no
        matter what the buffer remembers.
        """
        activity = self._broker.activity_snapshot(str(topic_id))
        if activity is not None:
            return {
                "turn_id": activity["turn_id"],
                "silent_for_s": activity["idle_for_s"],
            }
        now = time.monotonic()
        for turn_id, live_topic in self._live_topics.items():
            if live_topic != topic_id or turn_id not in self._live:
                continue
            frame_at = self._last_frame_at.get(turn_id)
            return {
                "turn_id": turn_id,
                # None means the bookkeeping is off (an entry without a frame
                # stamp); the caller treats an unknown gap as "not proof of
                # life" rather than inventing a fresh one.
                "silent_for_s": None if frame_at is None else round(now - frame_at, 1),
            }
        return None

    def project_queue_depth(self, project_id: uuid.UUID | str) -> int:
        """Turns currently waiting on this project's concurrency semaphore."""
        return self._project_waiting.get(str(project_id), 0)

    def submit(
        self,
        chat_service,
        topic_id: uuid.UUID,
        *,
        author: str,
        content: str,
        summon: bool,
        reply_to: str | None = None,
        attachments: list[dict] | None = None,
        is_resume: bool = False,
        resume_reason: str | None = None,
        nudge_event: str | None = None,
        nudge_meta: dict | None = None,
        continuation_id: uuid.UUID | None = None,
        provision_actor: Actor | None = None,
        on_done: Callable[[], None] | None = None,
    ) -> uuid.UUID:
        """Start a turn in the background; return its turn_id immediately. Turns
        on the same topic serialize on ChatService's per-topic lock (so a second
        submit queues behind the first).

        ``nudge_event`` is the ONE LINE the room sees for a platform-initiated
        turn; ``nudge_meta`` is that event's structured payload (see
        `platform_notices.notice`), which is where the long text goes — the CI
        log, the check output, the provider's own words. ``content`` stays the
        agent's prompt either way, so what 芝士 receives never changes when this
        pair does.

        ``continuation_id`` names the logical unit of work. A fresh turn starts
        one (defaulting to its own turn id); a re-send INHERITS the interrupted
        turn's, which is what lets a side effect the first attempt already
        performed be recognised as done — see domain.idempotency.keys.

        ``on_done`` fires when this turn's task finishes, whatever the outcome.
        It exists for callers that COALESCE work onto a running turn (母子传话,
        `domain.topic.relay`) and therefore need the moment the topic is free
        again; it is not an error channel and never sees the result. It runs on
        the event loop as a done-callback, so it must not block and must not
        raise — an exception there would only reach the loop's handler."""
        turn_id = uuid.uuid4()
        task = asyncio.create_task(
            self._run(
                chat_service,
                topic_id,
                turn_id,
                author=author,
                content=content,
                summon=summon,
                reply_to=reply_to,
                attachments=attachments,
                is_resume=is_resume,
                resume_reason=resume_reason,
                nudge_event=nudge_event,
                nudge_meta=nudge_meta,
                continuation_id=continuation_id or turn_id,
                provision_actor=provision_actor,
            )
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        if on_done is not None:
            task.add_done_callback(lambda _task: _fire_on_done(on_done))
        return turn_id

    def subscribe_messages(self) -> None:
        """Attach the process-owned runner to accepted room messages."""
        self._broker.subscribe_messages(self._receive_message)

    def _receive_message(self, chat_service, topic_id, turn_id, **message) -> None:
        task = asyncio.create_task(
            self._consume_message(chat_service, topic_id, turn_id, **message)
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _consume_message(
        self, chat_service, topic_id, turn_id, **message
    ) -> None:
        # Order each recipient's messages. Waiting for another agent's turn must
        # not block a follow-up addressed to the agent that is still running.
        key = (topic_id, message["recipient_handle"])
        lock = self._message_locks.setdefault(key, asyncio.Lock())
        # The window this and the two lines below measure runs from a message
        # being durably received to its turn starting to assemble. Every other
        # stretch of a turn is timed; this one never was, and it is not small —
        # it holds a queue behind the recipient's other messages, a live-delivery
        # attempt that reaches the database, and the credit and concurrency gates.
        # Measured through it, a turn only shows a gap with nothing in it.
        admission_started = time.monotonic()
        try:
            async with lock:
                logger.info(
                    "chat_admission_timing topic=%s turn=%s phase=message_lock "
                    "elapsed_ms=%.3f unix_ms=%.3f",
                    topic_id,
                    turn_id,
                    (time.monotonic() - admission_started) * 1000,
                    time.time() * 1000,
                )
                if not message["summon"]:
                    if message["content"] or message["attachments"]:
                        await chat_service.merge_into_running_turn(
                            topic_id,
                            message["landed_user_block_ids"] or [turn_id],
                            message["content"],
                            message["author"],
                            message["attachments"],
                            **(
                                {"recipient_handle": message["recipient_handle"]}
                                if message["recipient_handle"] is not None
                                else {}
                            ),
                        )
                    await self._broker.publish(str(topic_id), {"type": "done"})
                    return
                if await self._deliver_message(
                    chat_service, topic_id, turn_id, **message
                ):
                    return
            logger.info(
                "chat_admission_timing topic=%s turn=%s phase=live_delivery_declined "
                "elapsed_ms=%.3f unix_ms=%.3f",
                topic_id,
                turn_id,
                (time.monotonic() - admission_started) * 1000,
                time.time() * 1000,
            )
            message.pop("landed_user_block_ids")
            message.pop("live_delivery_expected")
            await self._run(chat_service, topic_id, turn_id, **message)
        except Exception:
            logger.exception(
                "agent message subscription failed topic=%s turn=%s", topic_id, turn_id
            )
            await self._broker.publish(
                str(topic_id),
                {
                    "type": "error",
                    "message": "Agent message delivery failed",
                },
            )

    def submit_kickoff(
        self,
        chat_service,
        topic_id: uuid.UUID,
        *,
        prompt: str | None = None,
        turn_id: uuid.UUID | None = None,
    ) -> uuid.UUID:
        """A platform-event turn (spec §8.4): 分身自动开工 after a split/upgrade
        (default prompt), or the parent digesting a returned conclusion (custom
        prompt). No human message is posted — the agent speaks for itself; the
        pre-built kickoff frame stream rides the same _run pipeline (telemetry,
        timeout, failure events) via the `frames` override."""
        # Imported here, not at module scope: chat imports this module back.
        from app.domain.agent.harness.prompt import KICKOFF_PROMPT

        turn_id = turn_id or uuid.uuid4()
        frames = chat_service.kickoff(topic_id=topic_id, turn_id=turn_id, prompt=prompt)
        task = asyncio.create_task(
            self._run(
                chat_service,
                topic_id,
                turn_id,
                author="system",
                # The RESOLVED prompt, not the argument: `kickoff` substitutes
                # KICKOFF_PROMPT for None, and this text is the only copy the
                # orphan sweep has if a deploy kills the turn before the session
                # hears it. Recording "" would make a 分身's first turn the one
                # kind of work the platform cannot re-deliver.
                content=prompt or KICKOFF_PROMPT,
                summon=True,
                frames=frames,
            ),
            name=f"kickoff:{turn_id}",
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return turn_id

    def kickoff_pending(self, turn_id: uuid.UUID) -> bool:
        """Include admission queueing before the durable turn interval opens."""
        return any(
            not task.done() and task.get_name() == f"kickoff:{turn_id}"
            for task in self._tasks
        )

    #: The author on an interval the SESSION opened for itself. Deliberately not
    #: "system": a platform-event turn is one the platform asked for and could
    #: ask for again, and this is neither — nobody wrote its prompt, so there is
    #: nothing to re-send. `names_a_person` already reads it as not-a-person, so
    #: `turn_author_for` keeps answering None the way it does for 平台 turns.
    SELF_STARTED_AUTHOR = "session"

    async def open_self_started_turn(
        self, chat_service, topic_id: uuid.UUID, turn_id: uuid.UUID
    ) -> None:
        """Register an interval for work the SESSION started on its own.

        A session works without being asked whenever one of its workers finishes:
        the completion notice wakes it and it runs a whole turn off that. The
        platform fed it nothing, so until now no interval was ever opened — which
        made such a turn the one kind this table cannot see, and therefore the one
        kind no sweep can ever find wedged.

        Opened DELIVERED, and that is not laziness: `close_for_topic` only closes
        delivered intervals because 投喂 → Stop is what an interval means for a fed
        turn, so an undelivered row here would be one nothing could ever close.
        What delivery guards against — a Stop from the previous conversation
        closing a turn whose prompt is still in flight — cannot happen to this
        one: it is opened BY output from the very session whose Stop ends it.

        Registered in `_last_frame_at`/`_live_topics` but NOT in `_live`: no
        coroutine of ours is running it, and claiming otherwise would have the
        sweep try to cancel a task that does not exist. The frame stamp is what
        lets silence be judged at all — see `_wedged_turns`.
        """
        self._recent.append(
            {
                "turn_id": str(turn_id),
                "topic_id": str(topic_id),
                "continuation_id": str(turn_id),
                "author": self.SELF_STARTED_AUTHOR,
                "summon": False,
                "is_resume": False,
                "status": "running",
                "started_at": time.time(),
                "first_output_s": None,
                "tools": 0,
                "duration_s": None,
                "detail": None,
            }
        )
        self._last_frame_at[str(turn_id)] = time.monotonic()
        self._live_topics[str(turn_id)] = topic_id
        now = _utcnow()
        await _open_turn(
            chat_service.session_factory,
            turn_id=turn_id,
            topic_id=topic_id,
            continuation_id=turn_id,
            author=self.SELF_STARTED_AUTHOR,
            content="",
            is_resume=False,
            # Nothing to re-send: there was no prompt. This is what stops the
            # sweep from ever picking one of these as a re-send candidate.
            resendable=False,
            started_at=now,
            # Stamped in the same write, not after it: a row that exists for even
            # a moment without it is a row a Stop landing in that moment cannot
            # close, and nothing would ever come back to close it.
            delivered_at=now,
        )

    def close_self_started_turn(self, turn_id: uuid.UUID) -> None:
        """Drop the in-memory marks for a self-started turn that has stopped.

        The durable row is closed by the Stop that ends it, like any other; these
        maps have no `finally` to fall out of, because no coroutine owns one.
        """
        self._forget_turn(turn_id)
        for rec in reversed(self._recent):
            if rec.get("turn_id") == str(turn_id):
                rec["status"] = "done"
                return

    def _forget_turn(self, turn_id: uuid.UUID) -> None:
        self._last_frame_at.pop(str(turn_id), None)
        self._live_topics.pop(str(turn_id), None)

    # Past this age an orphan is left for a person rather than acted on: a
    # deploy that stranded a prompt this long ago is one nobody still wants
    # re-sent unasked, so the sweep hands it over instead. Do NOT reach for this
    # number when an orphan goes unnoticed — the bug was never where the line
    # sits, it was that crossing it did nothing at all. Raising it only makes the
    # silence last longer.
    ORPHAN_STALE_S = 7200

    # A periodic sweep ignores registry entries younger than this. `_execute`
    # writes the disk registry and `_live` with no await between them, so there
    # is no window today — this is insurance against a refactor introducing one,
    # because the cost of getting it wrong is running a live turn twice.
    SWEEP_MIN_AGE_S = 60.0

    # How long a registered turn may emit NOTHING — no Block, no frame — before
    # the sweep calls it wedged. The floor is set by the longest a healthy turn
    # can legitimately stay quiet: one blocking tool call, whose own ceiling is
    # 10 minutes. 30 gives that 3x headroom, because the expensive mistake here
    # is the false positive (cancelling work that was fine), not the slow catch
    # — the incident this guards against ran for EIGHT HOURS.
    SILENT_TURN_S = 1800.0

    def _adopted(
        self,
        chat_service,
        record: TurnRecord,
        wedged: set[uuid.UUID],
    ) -> bool:
        """Is this turn still being worked, by something this process is not
        running? Then it is not an orphan and the sweep leaves it alone.

        Both halves have to hold. The screen must still be reachable — that is
        what a backend restart does NOT take with it, and what
        `recover_sessions` re-establishes on the way up. And the prompt
        must have reached it: a screen that is alive but never heard the task is
        not working on anything, and treating it as adopted would strand the
        message forever.

        A wedged turn is excluded by definition — its screen may well answer a
        `has_live_screen` probe while the thing behind it is dead, which is the
        whole reason silence is judged separately.
        """
        if record.turn_id in wedged or not record.delivered:
            return False
        try:
            return bool(chat_service.has_live_screen(record.topic_id))
        except Exception:  # noqa: BLE001 — an unanswerable probe is not a yes
            logger.exception("live-screen probe failed for %s", record.topic_id)
            return False

    async def _wedged_turns(
        self,
        open_turns: dict[uuid.UUID, TurnRecord],
        last_activity: (
            Callable[[set[uuid.UUID]], Awaitable[dict[uuid.UUID, datetime]]] | None
        ),
        silence_s: float,
        now: datetime,
    ) -> set[uuid.UUID]:
        """Of the turns this process is watching, which have gone quiet on BOTH
        signals? Startup passes no `last_activity` — there is nothing to judge
        then, so the probe is skipped entirely.

        Watching, not running: a self-started turn has no coroutine and so is
        never in `_live`, but it is the one kind of turn that CANNOT be caught by
        the other branch either. Its screen is the room's own and answers a
        liveness probe long after the work behind it stopped, so `_adopted` keeps
        saying yes and its interval would stay open forever. What it does have is
        a frame stamp, which is exactly what silence is judged on.
        """
        candidates = {
            tid
            for tid in open_turns
            if str(tid) in self._live or str(tid) in self._last_frame_at
        }
        if not candidates or last_activity is None:
            return set()
        try:
            blocks_at = await last_activity(
                {open_turns[tid].topic_id for tid in candidates}
            )
        except Exception:  # noqa: BLE001 — a failed probe must not cancel turns
            logger.exception("orphan sweep: last-activity probe failed")
            return set()
        mono = time.monotonic()
        wedged: set[uuid.UUID] = set()
        for tid in candidates:
            record = open_turns[tid]
            # Wall-clock age of the newest Block in the topic...
            last_block = blocks_at.get(record.topic_id)
            block_quiet_s = (
                (now - last_block).total_seconds()
                if last_block is not None
                else record.age_s(now)
            )
            # ...versus the newest frame this process published for this turn.
            # A turn we are running always has an entry (set at start), so a
            # missing one means the bookkeeping is off — treat it as fresh and
            # let the not-in-`_live` branch handle it instead of guessing.
            frame_at = self._last_frame_at.get(str(tid))
            if frame_at is None:
                continue
            frame_quiet_s = mono - frame_at
            if min(block_quiet_s, frame_quiet_s) > silence_s:
                wedged.add(tid)
                logger.warning(
                    "turn %s is wedged: no block for %ss, no frame for %ss",
                    tid,
                    round(block_quiet_s),
                    round(frame_quiet_s),
                )
        return wedged

    def _cancel_wedged(self, turn_id: str, topic_id: uuid.UUID) -> None:
        """Tear down a turn whose task is alive but producing nothing. Its own
        `finally` does the rest of the cleanup (gate release, `_live` removal)
        once the cancellation lands at its next await point."""
        task = self._live.get(turn_id)
        if task is None or task.done():
            return
        task.cancel()
        logger.warning("cancelled wedged turn %s on topic %s", turn_id, topic_id)

    async def resume_orphans(self, chat_service) -> int:
        """Startup sweep. `_live` is empty at boot, so every open interval is by
        definition an orphan of the previous process generation — which makes
        this exactly `sweep_orphans` with the young-entry guard switched off
        (nothing can be racing us: lifespan runs before the app serves)."""
        return await self.sweep_orphans(chat_service, min_age_s=0.0)

    async def sweep_orphans(
        self,
        chat_service,
        *,
        min_age_s: float | None = None,
        last_activity: (
            Callable[[set[uuid.UUID]], Awaitable[dict[uuid.UUID, datetime]]] | None
        ) = None,
        silence_s: float | None = None,
    ) -> int:
        """Claim every turn whose interval is open but not actually progressing,
        and make its fate VISIBLE in the topic. Returns how many were auto-resumed.

        Why this is not startup-only (the 101/173-minute incident, 2026-08-11):
        a turn dying does not imply the platform restarted. A container recreate,
        an OOM-killed child, a sandbox image swap — each kills a turn while the
        process lives happily on. Nothing then ever re-reads the open intervals,
        so one sits there and the topic keeps reporting `active` with a last block
        that is a command which never returned. That is indistinguishable, to a
        human reading the platform, from a slow test run.

        A turn is dead in one of two ways, and BOTH must be caught — the second
        is what let three topics lie silent for 8 hours on 2026-08-11 while this
        process was up the whole time:

        1. Not in `_live` — a previous generation of the process started it and
           died. An open interval outlives the process; `_live` does not.
        2. In `_live` but SILENT — the task is still parked in the event loop,
           but nothing is coming out of it. What died is the thing it was driving
           (the sandbox container, the provider stream), not the task. The wall
           clock ceiling does not save us here: a backend that signalled a large
           `turn_ceiling` can legitimately hold the deadline open for hours.

        Silence is judged on two signals, taking the more recent — a turn is only
        dead if BOTH are cold. `last_activity` is the topic's newest Block (the
        signal a human can verify from the UI, and the one that survives a wrong
        `_live`); `_last_frame_at` is the newest frame this process published,
        which also counts tool calls — those persist no Block, so a long
        tool-only stretch is alive but invisible to the DB alone. Erring toward
        "still alive" is deliberate: resuming a live turn is worse than noticing
        a dead one late.

        Not every turn missing from `_live` is dead, and that is the whole of
        what this sweep learned to stop doing. A backend restart kills the
        coroutine WAITING on a turn; the claude out in the execution environment
        keeps working, and `recover_sessions` finds its screen again on the
        way up. Such a turn is adopted — left running, its interval left open,
        and closed by the Stop that screen eventually sends, exactly as if
        nothing had happened. Nothing is said, because nothing broke.

        That used to be inferred rather than known. The sweep read the topic's
        blocks and its hook spool looking for traces that claude had been
        talking, because the platform had no way to ask whether the screen was
        still there. It can ask now, so the tracing is gone.

        What is left needs a remedy:

        - WEDGED (in `_live`, silent): the thing the task drove — the sandbox
          container, the provider stream — is what died, taking its claude with
          it. Cancel it and hand the topic to a person: re-running a turn only
          re-enters the machine that just died under it, so the platform does
          not do it on its own. A person looks, then re-@s 芝士, which reconnects
          to the `--resume`d session that still holds the conversation.
        - STRANDED (no screen, or a screen that never heard the prompt): the
          task never reached anyone. One re-send per topic, and it is the
          ORIGINAL text (the pending-message mechanism re-hands it verbatim),
          never a "接着干" nudge a task-less claude cannot act on. Too old, or
          itself a re-send, and the topic is handed to a person instead.
        - DELIVERED but no screen answers: the prompt got through and the
          container it got through to is gone. This one is silent — collect
          whatever the dead screen parked and stop there. NOT re-prompted, even
          though the saved session would make it possible: a device's screens
          come back when the device dials in, which can be minutes after this
          sweep runs, so "no screen" at startup routinely means "not yet".
          Re-prompting on that would re-send every device topic on every deploy.

        A turn the platform does not re-run is a turn someone has to pick up by
        hand, and they can only do that if the topic says so — silence is the
        failure mode, not the loud recovery."""
        open_turns = await _open_turns(chat_service.session_factory)
        if not open_turns:
            return 0
        if min_age_s is None:
            min_age_s = self.SWEEP_MIN_AGE_S
        if silence_s is None:
            silence_s = self.SILENT_TURN_S
        now = _utcnow()
        old_enough = {
            tid: record
            for tid, record in open_turns.items()
            if record.age_s(now) >= min_age_s
        }
        wedged = await self._wedged_turns(old_enough, last_activity, silence_s, now)
        orphans = {
            tid: record
            for tid, record in old_enough.items()
            if (str(tid) not in self._live or tid in wedged)
            and not self._adopted(chat_service, record, wedged)
        }
        if not orphans:
            return 0

        # Claim them by closing their intervals: a turn this sweep is deciding
        # the fate of must not be decided again by the next one. Only these ids
        # — a turn that opened while the activity probe awaited is still open,
        # and a running turn no sweep can see is how the next death goes silent
        # again, which is the whole bug.
        await _close_turns(chat_service.session_factory, orphans)
        for turn_id in orphans:
            # A turn with a coroutine gets its marks dropped by that coroutine's
            # own `finally`; one without (a self-started turn, or anything left
            # by a dead process generation) has nobody to do it, and a stale
            # frame stamp would keep offering the same corpse to every sweep.
            if str(turn_id) not in self._live:
                self._forget_turn(turn_id)
        remedied = 0
        # --- wedged turns: their claude died WITH whatever they were driving.
        # Cancel to release the topic lock, tell the room once, and stop —
        # re-running only re-enters the machine that just died, so a person
        # picks it up and re-@s 芝士 when the environment is back.
        wedged_topics: set[uuid.UUID] = set()
        for turn_id, record in orphans.items():
            if turn_id not in wedged:
                continue
            topic_id = record.topic_id
            age_s = record.age_s(now)
            # A wedged turn still owns the topic lock. Cancelling is not tidiness
            # — even a turn nobody will re-run must let the next human message
            # through.
            self._cancel_wedged(str(turn_id), topic_id)
            # One event per topic: a queued turn parked behind a wedged one looks
            # wedged too, and a second notice for the same room is only noise.
            if topic_id in wedged_topics:
                continue
            wedged_topics.add(topic_id)
            how = f"卡死了：{round(age_s / 60)} 分钟里一个字都没输出，已强制结束"
            await self._post_orphan_event(
                chat_service,
                topic_id,
                f"芝士上一轮{how}，平台不会自动重试",
                notice(
                    EVENT_TURN_TIMEOUT,
                    severity=SEVERITY_WARN,
                    # 平台不再自动重试 —— 要有人看一眼、再 @ 它。
                    who=WHO_HUMAN,
                    detail=(
                        "已完成的改动都还在工作区里。平台不会自动重跑"
                        "（重跑只会再进一次刚死掉的机器）——需要继续的话，"
                        "有人看一眼后 @ 芝士，它会从断点接着做。"
                    ),
                    detail_label="详细说明",
                ),
            )
            logger.info(
                "orphan turn %s cancelled as wedged (age=%ss); no auto-retry",
                turn_id,
                round(age_s),
            )
            # No increment: the return counts remedial RE-SENDS scheduled, and a
            # wedged turn is cancelled and announced but never re-run — the same
            # zero the stale-wedged drop always returned.
        # --- what is left after adoption: no screen answers for this topic, or
        # one does and never heard the prompt. Decide per TOPIC, because a
        # remedy is a prompt into a room and one room takes one.
        by_topic: dict[uuid.UUID, list[TurnRecord]] = {}
        for turn_id, record in orphans.items():
            if turn_id in wedged:
                continue
            by_topic.setdefault(record.topic_id, []).append(record)
        for topic_id, entries in by_topic.items():
            remedied += await self._settle_restart_orphans(
                chat_service,
                topic_id,
                entries,
                now,
                # A topic the wedged branch already remedied gets no second
                # action — its restart orphans are folded in, loudly.
                allow_actions=topic_id not in wedged_topics,
            )
        return remedied

    async def _settle_restart_orphans(
        self,
        chat_service,
        topic_id: uuid.UUID,
        entries: list[TurnRecord],
        now: datetime,
        *,
        allow_actions: bool = True,
    ) -> int:
        """One topic's remedy for turns that reached nobody.

        Returns how many remedial prompts were scheduled (0 or 1). Everything
        that got through has already been excluded upstream by `_adopted` — what
        arrives here is a topic whose screen is gone, or whose screen never
        heard the prompt.

        A re-send is for the newest re-sendable turn (see `_execute` for what
        that means): the pending-message mechanism re-hands its ORIGINAL text
        (an interrupted turn never stamps its inputs consumed), and the rest are
        folded into the same prompt.

        One narrow exception to "nobody heard it": a process can die between the
        transport accepting the write and the record of it, leaving a turn that
        DID arrive looking undelivered. `turns_that_produced_something` closes
        that window, and a failure to ask counts as "it arrived" — when we
        cannot know, sending again is the riskier side.

        Whose turn it was does not enter into it. A deploy that strands 平台's
        own work — a 分身's kickoff, 验收卡被驳回, CI 红了 — strands it just as
        permanently as a person's message, and the room shows nothing either
        way. Re-sending it is what keeps the platform working rather than merely
        quiet."""
        delivered = {record.turn_id for record in entries if record.delivered}
        probe_ok = False
        try:
            delivered |= await chat_service.turns_that_produced_something(
                [record.turn_id for record in entries]
            )
            probe_ok = True
        except Exception:  # noqa: BLE001 — a failed probe must not kill the sweep
            logger.exception("orphan block probe failed for %s", topic_id)
        attach = bool(delivered) or not probe_ok

        resend: TurnRecord | None = None
        if allow_actions and probe_ok:
            candidates = [
                record
                for record in entries
                if record.turn_id not in delivered
                # Decided where the turn starts (see `_execute`): it holds for a
                # person's message and for every platform task alike.
                and record.resendable
                and record.age_s(now) <= self.ORPHAN_STALE_S
            ]
            if candidates:
                resend = max(candidates, key=lambda record: record.started_at)

        # 一次部署把这个话题的轮次打断了，接下来会发生什么，决定要不要说话。
        #
        # 绝大多数情况平台自己就收拾干净了：送达过的，现场根本没死，订阅跟着屏幕
        # 活（#508），它送回的东西继续实时落回房间；没送达的，下面无条件原样重发
        # 一次。两种都不出声 —— 期望的就是它正常工作，正常工作没有可通报的。
        #
        # （这条事件原来每个被打断的轮次都发一次，理由是 #316：部署静默打断轮次、
        # 房间里不留痕迹，查的人只能猜，为此误诊过两次（#188）。那是取消 turn 之前
        # 的世界 —— 那时后端一死，会话的输出要等 spool 收口才浮出来，房间看着像
        # 停了。现在没有那个断裂，理由跟着不成立。）
        #
        # 剩下真正会伤到人的只有一种：消息卡在「已落库」和「已送进会话」中间，
        # 而且平台明确不会替他重发。这时候用户的话是真的消失了，芝士永远不会回，
        # 房间里也没有任何别的东西会显示这件事 —— 不说，没人知道要再问一次。
        # 两条路能走到这儿：
        #
        # - 搁得太久（越过 ORPHAN_STALE_S，2 小时）。扫描每 300 秒一轮、外加启动
        #   时一次，所以要越过它，平台得连着两小时没能扫 —— 那是一次宕机，不是一
        #   次部署。这时自动重发多半已经不是他要的了，得他自己决定还发不发。
        # - 这轮本身是一次重发（`resendable` 为假）。重发只把原始消息递一次，
        #   打断了就不连着再递，平台不会自动跑第二次。
        stranded = allow_actions and probe_ok and not attach and resend is None
        if stranded and entries:
            newest = max(entries, key=lambda record: record.started_at)
            age_s = newest.age_s(now)
            stale = age_s > self.ORPHAN_STALE_S
            # 平台提示统一契约: 房间里一行 `text`，展开才看的长文进 meta.detail。
            text = (
                f"这条消息没送到芝士那边，已经搁了 {round(age_s / 60)} 分钟"
                if stale
                else "上一次重发被平台重启打断了，没送到芝士那边"
            )
            detail = (
                "没有迹象表明消息送到了芝士那边，而它搁置得太久，"
                "自动重发多半已经不是你要的了。"
                "需要继续的话 @ 芝士，之前的消息会一并带上。"
                if stale
                else (
                    "重发只发一次，不连着自动重试。已完成的改动都还在工作区里"
                    "—— 需要继续的话 @ 芝士，它会从断点接着做。"
                )
            )
            await self._post_orphan_event(
                chat_service,
                topic_id,
                text,
                notice(
                    EVENT_DEPLOY_INTERRUPTED,
                    severity=SEVERITY_WARN,
                    # 平台不再自动做任何事了 —— 这条要人来。
                    who=WHO_HUMAN,
                    detail=detail,
                    detail_label="详细说明",
                ),
            )
        if not allow_actions:
            return 0
        if attach:
            # Collect whatever the surviving claude already parked (a Stop
            # included) — and whatever it sends next lands the same way via the
            # hooks endpoint's own settle trigger.
            try:
                chat_service.schedule_spool_settle(topic_id)
            except Exception:  # noqa: BLE001 — best-effort, the event already told the room
                logger.exception("spool settle scheduling failed for %s", topic_id)
            logger.info(
                "orphan turn(s) %s attached on topic %s (delivered=%d)",
                [record.turn_id for record in entries],
                topic_id,
                len(delivered),
            )
        if resend is not None:
            self._schedule_resend(
                chat_service,
                topic_id,
                3.0,
                resend.content,
                continuation_id=resend.continuation_id,
            )
            logger.info("orphan turn %s scheduled for re-send", resend.turn_id)
            return 1
        return 0

    async def _post_orphan_event(
        self,
        chat_service,
        topic_id: uuid.UUID,
        text: str,
        meta: dict | None = None,
    ) -> None:
        """Persist + broadcast an orphan verdict. Best-effort by design: for a
        resumed orphan the resume matters more than the notice, and for a dropped
        one there is nothing left to fail into.

        ``meta`` is the 平台提示统一契约 payload — chiefly `who`, which is what
        lets someone decide "does this need me?" without reading the sentence."""
        try:
            block = await chat_service.post_system_event(topic_id, text, meta=meta)
            if block is not None:
                await self._broker.publish(
                    str(topic_id), {"type": "event_block", "block": block}
                )
        except Exception:  # noqa: BLE001 — a notice must never break the sweep
            logger.exception("orphan event failed for %s", topic_id)

    # The re-send opener's wording (#316): name the platform as the cause —
    # "被部署中断" — never "AI 服务返回错误" for a failure the deploy made.
    RESEND_REASON = "上一轮被平台部署中断，消息没送到芝士那边，原样重发一次"

    #: Same contract for the other platform-caused silence: the room's tools
    #: vanished mid-turn, so 芝士 answered where nobody could hear it.
    TOOLS_REASON = (
        "上一轮平台的工具通道断了，芝士的回复没能发进房间；已经接回来，原样重发一次"
    )

    async def _recover_silent_turn(
        self,
        chat_service,
        topic_id: uuid.UUID,
        content: str,
        continuation_id: uuid.UUID | None,
    ) -> None:
        """Put the room's tools back if they were gone, then re-deliver."""
        try:
            recovered = await chat_service.recover_native_tools(topic_id)
        except Exception:  # noqa: BLE001 — recovery must never fail a finished turn
            logger.exception("tool recovery check failed for topic %s", topic_id)
            return
        if not recovered:
            return
        await self._post_orphan_event(
            chat_service,
            topic_id,
            "平台的工具通道断了，刚才那条消息芝士没能回进房间；已经接回来，正在重发",
            self._TOOLS_RECOVERED_META,
        )
        self.submit(
            chat_service,
            topic_id,
            author="system",
            content=content,
            summon=True,
            is_resume=True,
            resume_reason=self.TOOLS_REASON,
            continuation_id=continuation_id,
        )

    def _schedule_resend(
        self,
        chat_service,
        topic_id: uuid.UUID,
        after_s: float,
        content: str,
        *,
        continuation_id: uuid.UUID | None = None,
    ):
        """One bounded re-delivery of a prompt with NO evidence of arrival.

        The prompt this turn actually runs with is the ORIGINAL text — an
        interrupted turn never stamps its inputs consumed, so the pending-
        message mechanism re-hands them verbatim; ``content`` is only the
        fallback for the rare topic with nothing pending. It is a re-delivery,
        not a "pick up where you left off" nudge, which would be meaningless to
        a claude that never heard the task. `is_resume=True` keeps it from ever
        chaining further automatic turns, and the inherited continuation keeps
        any side effect that somehow DID land from being repeated."""

        async def _later() -> None:
            await asyncio.sleep(after_s)
            self.submit(
                chat_service,
                topic_id,
                author="system",
                content=content,
                summon=True,
                is_resume=True,
                resume_reason=self.RESEND_REASON,
                continuation_id=continuation_id,
            )

        hold(
            asyncio.create_task(_later()),
            self._tasks,
            name=f"orphan-resend-{topic_id}",
        )
        logger.info("scheduled orphan re-send for topic %s in %.0fs", topic_id, after_s)

    # Platform copy for the queue event — structured, never 芝士's own words.
    @staticmethod
    def _queued_text(ahead: int) -> str:
        if ahead <= 0:
            return "项目同时进行的轮次已满，这轮先排队"
        return f"项目同时进行的轮次已满，这轮先排队，前面还有 {ahead} 个"

    #: 工具断了是平台的事，平台自己接回来并重发；房间里的人不用动手。
    _TOOLS_RECOVERED_META = notice(
        EVENT_TOOLS_RECOVERED,
        severity=SEVERITY_WARN,
        who=WHO_PLATFORM,
        detail="重发只发一次。芝士上一轮说的话留在执行会话里，没有发进房间。",
        detail_label="接下来会发生什么",
    )

    #: 排队不是故障：平台自己会往前推，没人需要动手。
    _QUEUED_META = notice(
        EVENT_TURN_QUEUED,
        severity=SEVERITY_INFO,
        who=WHO_PLATFORM,
        detail="前面的轮次结束就自动开跑，不用重发。",
        detail_label="接下来会发生什么",
    )

    async def _post_event(
        self,
        chat_service,
        topic_id: uuid.UUID,
        turn_id: uuid.UUID,
        text: str,
        *,
        meta: dict | None = None,
    ) -> bool:
        """Persist + broadcast a platform system event (queue/refusal). Reuses
        the post_system_event + broker path the nudge mechanism uses."""
        try:
            block = await chat_service.post_system_event(
                topic_id, text, turn_id, meta=meta
            )
        except Exception:  # noqa: BLE001 — visibility is best-effort
            logger.exception("failed to post admission event for %s", topic_id)
            return False
        if block is not None:
            await self._broker.publish(
                str(topic_id), {"type": "event_block", "block": block}
            )
        return block is not None

    async def _admit(
        self, chat_service, topic_id: uuid.UUID, turn_id: uuid.UUID
    ) -> tuple[str, asyncio.Semaphore | None]:
        """Admission control (spec §9.1 算力额度真实化), before any execution:

        - credits exhausted → ("reject", None): the caller refuses the turn.
        - project concurrency full → queue on the project semaphore (FIFO),
          after posting a visible "排队中" system event. Returns ("ok", sem)
          with the ACQUIRED semaphore (caller must release).
        - topic unknown / policy lookup failed → ("ok", None): admit ungated;
          the turn itself surfaces the real error.
        """
        try:
            policy = await chat_service.work_policy(topic_id)
        except Exception:  # noqa: BLE001 — admission must never kill a turn
            logger.exception("work_policy failed for %s; admitting", topic_id)
            policy = None
        if policy is None:
            return "ok", None
        if policy["credits_exhausted"]:
            logger.info("turn %s rejected: credits exhausted", turn_id)
            return "reject", None
        key = policy["project_id"]
        sem = self._project_sems.get(key)
        if sem is None:
            sem = asyncio.Semaphore(policy["max_concurrent_turns"])
            self._project_sems[key] = sem
        if sem.locked():
            ahead = self._project_waiting.get(key, 0)
            await self._post_event(
                chat_service,
                topic_id,
                turn_id,
                self._queued_text(ahead),
                meta=self._QUEUED_META,
            )
            logger.info("turn %s queued (project=%s ahead=%s)", turn_id, key, ahead)
        self._project_waiting[key] = self._project_waiting.get(key, 0) + 1
        try:
            await sem.acquire()
        finally:
            left = self._project_waiting.get(key, 1) - 1
            if left > 0:
                self._project_waiting[key] = left
            else:
                self._project_waiting.pop(key, None)
        return "ok", sem

    async def _refuse_exhausted(
        self,
        chat_service,
        topic_id: uuid.UUID,
        turn_id: uuid.UUID,
        *,
        author: str,
        content: str,
        reply_to: str | None,
        attachments: list[dict] | None,
        is_message_turn: bool,
        message_landed: bool = False,
    ) -> None:
        """Refuse a turn for exhausted credits. A human's message still lands
        (speaking is free — only the AI turn is metered): it goes through a
        summon=False converse pass, then the structured platform event says why
        芝士 isn't coming. The copy is the PLATFORM's, never the model's."""
        from app.domain.usage.credits import (
            CREDITS_EXHAUSTED_EVENT,
            CREDITS_EXHAUSTED_META,
        )

        channel = str(topic_id)
        if is_message_turn and not message_landed and (content or attachments):
            try:
                async for frame in chat_service.converse(
                    topic_id=topic_id,
                    author=author,
                    content=content,
                    summon=False,
                    turn_id=turn_id,
                    reply_to=reply_to,
                    attachments=attachments,
                ):
                    if frame.get("type") != "done":
                        await self._broker.publish(channel, frame)
            except Exception:  # noqa: BLE001 — still surface the refusal
                logger.exception("failed to land message for refused turn")
        posted = await self._post_event(
            chat_service,
            topic_id,
            turn_id,
            CREDITS_EXHAUSTED_EVENT,
            meta=CREDITS_EXHAUSTED_META,
        )
        await self._broker.publish(
            channel,
            {
                "type": "error",
                "message": CREDITS_EXHAUSTED_EVENT,
                "persisted": posted,
            },
        )
        self._recent.append(
            {
                "turn_id": str(turn_id),
                "topic_id": str(topic_id),
                "status": "rejected",
                "detail": "算力额度已用完，未执行",
                "started_at": time.time(),
            }
        )

    async def _deliver_message(
        self,
        chat_service,
        topic_id,
        turn_id,
        *,
        recipient_handle=None,
        live_delivery_expected=False,
        landed_user_block_id=None,
        landed_user_block_ids=None,
        content="",
        author="",
        attachments=None,
        **_message,
    ) -> bool:
        channel = str(topic_id)
        if recipient_handle is not None:
            if await chat_service.wait_for_recipient(topic_id, recipient_handle):
                live_delivery_expected = False
        if landed_user_block_id is not None and (content or attachments):
            delivered = await chat_service.merge_into_running_turn(
                topic_id,
                landed_user_block_ids or [landed_user_block_id],
                content,
                author,
                attachments,
                **(
                    {"recipient_handle": recipient_handle}
                    if recipient_handle is not None
                    else {}
                ),
            )
            if delivered is True:
                ack = await chat_service.ack_summon(landed_user_block_id, topic_id)
                if ack is not None:
                    await self._broker.publish(channel, {"type": "reaction", **ack})
                return True
            if delivered is False or live_delivery_expected:
                logger.warning(
                    "live delivery fell back to the queue (topic=%s, "
                    "block=%s, delivered=%s, live_expected=%s)",
                    topic_id,
                    landed_user_block_id,
                    delivered,
                    live_delivery_expected,
                )
                fallback_text, fallback_meta = delivery_fallback_notice()
                await self._post_event(
                    chat_service,
                    topic_id,
                    turn_id,
                    fallback_text,
                    meta=fallback_meta,
                )

        return False

    async def _run(
        self,
        chat_service,
        topic_id: uuid.UUID,
        turn_id: uuid.UUID,
        *,
        author: str,
        content: str,
        summon: bool,
        reply_to: str | None = None,
        attachments: list[dict] | None = None,
        is_resume: bool = False,
        resume_reason: str | None = None,
        nudge_event: str | None = None,
        nudge_meta: dict | None = None,
        continuation_id: uuid.UUID | None = None,
        provision_actor: Actor | None = None,
        # Human message already persisted by ``receive_message``. Its AI work is
        # still pending admission and may instead merge into a live turn.
        landed_user_block_id: uuid.UUID | None = None,
        recipient_handle: str | None = None,
        # Pre-built frame stream (kickoff turns). None → run a converse turn.
        frames: AsyncIterator[Frame] | None = None,
    ) -> None:
        channel = str(topic_id)
        # 算力闸 (spec §9.1): refuse on exhausted credits, queue when the
        # project's concurrent-turn ceiling is reached. Both states are posted
        # into the topic as platform system events, so people SEE why nothing
        # is streaming yet.
        admit_started = time.monotonic()
        verdict, gate = await self._admit(chat_service, topic_id, turn_id)
        logger.info(
            "chat_admission_timing topic=%s turn=%s phase=admitted verdict=%s "
            "elapsed_ms=%.3f unix_ms=%.3f",
            topic_id,
            turn_id,
            verdict,
            (time.monotonic() - admit_started) * 1000,
            time.time() * 1000,
        )
        if verdict == "reject":
            if isinstance(frames, AsyncGenerator):
                await frames.aclose()  # never-started kickoff stream: close it
            await self._refuse_exhausted(
                chat_service,
                topic_id,
                turn_id,
                author=author,
                content=content,
                reply_to=reply_to,
                attachments=attachments,
                # A human message turn lands its message even when refused;
                # resume/nudge/kickoff turns have nothing to land.
                is_message_turn=(
                    frames is None and not is_resume and nudge_event is None
                ),
                message_landed=landed_user_block_id is not None,
            )
            return
        lifecycle = {"started": False, "session_owned": False}
        try:
            if landed_user_block_id is not None:
                frames = chat_service.converse_prepared(
                    topic_id=topic_id,
                    author=author,
                    content=content,
                    turn_id=turn_id,
                    user_block_id=landed_user_block_id,
                    continuation_id=continuation_id,
                    provision_actor=provision_actor,
                    **(
                        {"recipient_handle": recipient_handle}
                        if recipient_handle is not None
                        else {}
                    ),
                )
            await self._execute(
                chat_service,
                topic_id,
                turn_id,
                author=author,
                content=content,
                summon=summon,
                reply_to=reply_to,
                attachments=attachments,
                is_resume=is_resume,
                resume_reason=resume_reason,
                nudge_event=nudge_event,
                nudge_meta=nudge_meta,
                continuation_id=continuation_id,
                provision_actor=provision_actor,
                frames=frames,
                lifecycle=lifecycle,
            )
        finally:
            # Close what this turn opened, unless the session took over THIS
            # turn — asking by id rather than trusting the handover.
            #
            # The mark gets opened on the first frame, and the frames that
            # arrive first are not this turn producing anything: they are the
            # room acknowledging the request (`ack_summon`'s ✅, a system event
            # block). `session_lifecycle` comes after them, so by the time the
            # runner learns a session will own the ending it has already opened
            # a mark — and used to decline to close it on the strength of that
            # frame alone.
            #
            # That holds only while the session opens its activity under the
            # SAME work id, which collapses the two into one entry. When a turn
            # begins on a topic that already has one running, the session reuses
            # the standing activity instead of opening a second, so the newer
            # turn is never started on the session's books and never ended
            # either. Nothing else publishes `turn_finished`, so the mark
            # outlives the turn by the length of the process: the room keeps
            # 正在思考, and `/health`'s drain count never reaches zero (#604).
            handed_over = lifecycle["session_owned"]
            session_ended_it = handed_over and chat_service.session_took_over(
                topic_id, turn_id
            )
            if lifecycle["started"] and not session_ended_it:
                await self._broker.publish(
                    channel, {"type": "turn_finished", "turn_id": str(turn_id)}
                )
            # Drop the liveness mark here, not in `_execute`: a turn killed by
            # task cancellation (CancelledError is a BaseException — it misses
            # every `except` inside `_execute`, including the registry cleanup)
            # must stop counting as live, so the next sweep can claim it. The
            # open interval deliberately survives — that is what gets it resumed.
            self._live.pop(str(turn_id), None)
            self._last_frame_at.pop(str(turn_id), None)
            self._live_topics.pop(str(turn_id), None)
            if gate is not None:
                gate.release()

    def _credential_is_known_expired(self, topic_id: uuid.UUID) -> bool:
        """Does the backend already KNOW this topic's model credential is expired?
        (#388 缺陷一.) True only when the injected lookup returns an expiry in the
        past. A lookup failure is swallowed (never block a turn on it) and reads as
        "not known-expired" — the honest default, since a missing signal is not
        evidence of death."""
        if self._credential_expiry_of is None:
            return False
        try:
            exp = self._credential_expiry_of(topic_id)
        except Exception:  # noqa: BLE001 — a lookup must never break a turn
            logger.exception("credential-expiry lookup failed for topic %s", topic_id)
            return False
        return exp is not None and exp <= time.time()

    async def _execute(
        self,
        chat_service,
        topic_id: uuid.UUID,
        turn_id: uuid.UUID,
        *,
        author: str,
        content: str,
        summon: bool,
        reply_to: str | None = None,
        attachments: list[dict] | None = None,
        is_resume: bool = False,
        resume_reason: str | None = None,
        nudge_event: str | None = None,
        nudge_meta: dict | None = None,
        continuation_id: uuid.UUID | None = None,
        provision_actor: Actor | None = None,
        # Pre-built frame stream (kickoff turns). None → run a converse turn.
        frames: AsyncIterator[Frame] | None = None,
        lifecycle: dict[str, bool] | None = None,
    ) -> None:
        channel = str(topic_id)
        lifecycle = (
            lifecycle
            if lifecycle is not None
            else {
                "started": False,
                "session_owned": False,
            }
        )
        continuation_id = continuation_id or turn_id
        # #388 缺陷一: set when the backend already knows this turn's credential is
        # dead. Read in the fuse-arming block (to cut the fuse short) and again in
        # the TimeoutError handler (to say the true reason and skip the auto-retry).
        # Bound here so it is always defined, even on an early failure path.
        credential_expired = False
        # Did this turn blame the MACHINE? Decides whether finishing counts as
        # evidence the machine is healthy (#186) — a turn that ends in a
        # host-scoped failure must not immediately clear the streak it just added.
        host_failed = False
        # Correlate: every log line anywhere inside this turn carries these ids.
        bind_context(turn=str(turn_id)[:8], topic=str(topic_id)[:8])
        t0 = time.monotonic()
        rec = {
            "turn_id": str(turn_id),
            "topic_id": str(topic_id),
            "continuation_id": str(continuation_id),
            "author": author,
            "summon": summon,
            "is_resume": is_resume,
            "status": "running",
            "started_at": time.time(),
            "first_output_s": None,
            "tools": 0,
            "duration_s": None,
            "detail": None,
        }
        self._recent.append(rec)
        # `_live` FIRST, and only then the durable row. Opening the interval is
        # a database round-trip, so a sweep can land in the gap — and a sweep
        # that sees this turn in `_live` and not in the table finds nothing to
        # claim, where the other order would show it a row with no task and read
        # it as a corpse. `SWEEP_MIN_AGE_S` is what actually protects the window;
        # a just-registered turn is too young to be judged either way.
        current = asyncio.current_task()
        if current is not None:
            self._live[str(turn_id)] = current
        self._last_frame_at[str(turn_id)] = time.monotonic()
        self._live_topics[str(turn_id)] = topic_id
        # The durable interval: if the PROCESS dies (deploy past the drain
        # ceiling, crash), startup finds it still open — a killed turn must
        # never just vanish.
        await _open_turn(
            chat_service.session_factory,
            turn_id=turn_id,
            topic_id=topic_id,
            continuation_id=continuation_id,
            author=author,
            content=content,
            is_resume=is_resume,
            resendable=bool(content.strip())
            and (not is_resume or resume_reason == self.RESEND_REASON),
            started_at=_utcnow(),
        )
        logger.info(
            "turn start: author=%s summon=%s resume=%s", author, summon, is_resume
        )
        try:
            # Wall-clock ceiling (R8): a wedged turn must not hold the topic lock
            # forever. On timeout the async-for exits, closing the converse
            # generator → its `async with` blocks unwind → the topic lock releases
            # and the in-container claude process is torn down.
            #
            # This wrap is transport-INDEPENDENT — one AgentWorkRunner singleton, same
            # `self._timeout` for every backend (SDK / tmux / device). Most
            # backends have no activity signal of their own, so this stays their
            # only ceiling. The tmux backend now has one (turn 活跃度检测:
            # hooks_substrate's two-layer idle-suspect + hard-ceiling loop can run
            # well past `self._timeout`) — it signals its actual ceiling back via
            # a `turn_ceiling` frame, and ONLY that reschedules this wrap
            # (`Timeout.reschedule`), relative to when the turn started so a late
            # frame can't silently grant more time than the backend promised.
            # Every other backend never emits this frame, so their behaviour here
            # is byte-for-byte unchanged.
            loop_start = asyncio.get_running_loop().time()
            # When the turn itself began, filled in by `prompt_delivered` below.
            # `loop_start` is not that moment: preparing the database, choosing a
            # backend and attaching the screen all happen after it.
            turn_start: float | None = None
            # Past `turn_start + ceiling_s` the turn is RECORDED as long, and
            # nothing else: the ceiling stopped being a deadline. Elapsed time
            # cannot tell an agent three hours into a refactor from a session
            # that is stuck, and the harness monitor now carries three checks
            # that each catch a specific way of being stuck (a process gone,
            # output with no tool call, an injected message never read). What a
            # wall clock alone could still end here is a turn that is working
            # and has not finished. So the only deadline this wrap keeps is the
            # cold-start fuse below, which asks a different question.
            ceiling_crossed = False

            # 冷启动看门狗: until the model has said ANYTHING, the wrap runs on a
            # much shorter fuse than the turn ceiling.
            #
            # A turn whose substrate never comes up is indistinguishable, from
            # out here, from one thinking hard — both are silence. So the ceiling
            # (900s for tmux) was what ended them, and for 900 seconds the topic
            # reported 进行中 while nothing existed to make progress. On
            # 2026-08-12 that took the whole dev platform down for 30 minutes:
            # every topic's turn started in the same second, ran with `tools=0`
            # and `first_output_s=None`, and each one occupied its full ceiling
            # before failing. The information needed to call it was there from
            # second one.
            #
            # The fuse only covers the gap BEFORE first output; once a `tool` or
            # `assistant_block` arrives the deadline is pushed out to the real
            # ceiling and this layer is gone for the rest of the turn. So a slow
            # turn is never cut short — only a turn that never started.
            #
            # `turn_ceiling` deliberately does NOT lift the fuse: chat.py emits it
            # up front, before the container is touched, so it proves a backend
            # was selected and nothing more. It is remembered and applied at first
            # output instead.
            first_output_fuse_s = self._first_output_timeout_s
            ceiling_s = self._timeout
            # #388 缺陷一: when the backend already knows this turn's model
            # credential is expired, the turn cannot produce a token — every
            # request is rejected (401/407) before a hook can flow. Don't spend the
            # full cold-start fuse guessing at container/disk/network: cut it to a
            # short grace (still long enough that a credential refreshed between
            # setup and now speaks first, retiring the fuse) so a silent turn fails
            # FAST and with the true reason. The definitive first-hand 401 lives in
            # the metering proxy (box infra, out of this repo); this is the part the
            # backend lands on its own from the expiry #386 already stamps.
            credential_expired = self._credential_is_known_expired(topic_id)
            if credential_expired and first_output_fuse_s:
                first_output_fuse_s = min(
                    first_output_fuse_s, self._credential_expired_fuse_s
                )
            if first_output_fuse_s:
                fuse_deadline = loop_start + min(first_output_fuse_s, self._timeout)
            else:
                fuse_deadline = None
            # No deadline until the fuse asks for one. `self._timeout` used to
            # be the starting value, standing in for a ceiling the backend had
            # not declared yet; the ceiling is recorded now, never scheduled, so
            # a wrap that started at `self._timeout` would cut every turn whose
            # fuse is disabled at exactly that mark, with nothing to say why.
            async with asyncio.timeout(None) as turn_deadline:
                if fuse_deadline is not None:
                    turn_deadline.reschedule(fuse_deadline)
                turn_frames = (
                    frames
                    if frames is not None
                    else chat_service.converse(
                        topic_id=topic_id,
                        author=author,
                        content=content,
                        summon=summon,
                        turn_id=turn_id,
                        reply_to=reply_to,
                        attachments=attachments,
                        is_resume=is_resume,
                        resume_reason=resume_reason,
                        nudge_event=nudge_event,
                        nudge_meta=nudge_meta,
                        continuation_id=continuation_id,
                        provision_actor=provision_actor,
                    )
                )
                async for frame in turn_frames:
                    kind = frame.get("type")
                    if kind == "session_lifecycle":
                        # Interactive providers hand lifecycle to the live
                        # subscription. Their request returns after injection;
                        # Stop or the session watchdog retires the indicator.
                        lifecycle["session_owned"] = True
                        turn_deadline.reschedule(None)
                        continue
                    # Proof of life for the silence check in sweep_orphans, taken
                    # before the `continue`s below so EVERY frame counts. A tool
                    # call persists no Block, so without this a turn legitimately
                    # grinding through tools looks identical to a wedged one.
                    self._last_frame_at[str(turn_id)] = time.monotonic()
                    # Measured from `turn_start` when the backend told us when
                    # the turn began, and from `loop_start` when it did not: a
                    # recorded fact must not depend on an optional frame.
                    ceiling_base = loop_start if turn_start is None else turn_start
                    if (
                        not ceiling_crossed
                        and asyncio.get_running_loop().time()
                        >= ceiling_base + ceiling_s
                    ):
                        ceiling_crossed = True
                        rec["ceiling_crossed_s"] = round(
                            asyncio.get_running_loop().time() - ceiling_base
                        )
                        logger.warning(
                            "turn %s is past its %ss ceiling and still going — "
                            "recorded, not ended (topic %s)",
                            turn_id,
                            round(ceiling_s),
                            topic_id,
                        )
                    if kind == "prompt_delivered":
                        # The transport accepted the write. Stamp the durable
                        # registry NOW: if this process dies a moment later, the
                        # sweep reads a fact instead of guessing from side
                        # effects that may not exist yet.
                        await _stamp_delivery(chat_service.session_factory, turn_id)
                        # The turn starts HERE, so the ceiling does too. Both
                        # clocks finally have a real base, and whichever comes
                        # first wins: the fuse still asks "did anything ever
                        # start" from `loop_start`, which is what makes the setup
                        # window its business, and the ceiling now asks "has this
                        # run too long" from the moment there was something to
                        # run.
                        turn_start = asyncio.get_running_loop().time()
                        continue
                    if kind == "turn_ceiling":
                        ceiling_s = float(frame.get("seconds", self._timeout))
                        # `topic_work()` reads this so `cheese status` reports the
                        # backend's REAL ceiling, not the generic outer default.
                        rec["ceiling_s"] = ceiling_s
                        # The frame's only remaining job here: it is emitted before
                        # the container is touched, so the generic default that
                        # truncated the fuse (`min(first_output_fuse_s,
                        # self._timeout)` above) was standing in for a ceiling
                        # nobody had declared. Now one is declared, the fuse gets
                        # its own full length back. The ceiling itself is recorded
                        # against `turn_start`, never scheduled.
                        if fuse_deadline is not None:
                            fuse_deadline = loop_start + max(0.0, first_output_fuse_s)
                            turn_deadline.reschedule(fuse_deadline)
                        continue
                    if not lifecycle["started"] and not lifecycle["session_owned"]:
                        await self._broker.publish(
                            channel,
                            {"type": "turn_started", "turn_id": str(turn_id)},
                        )
                        lifecycle["started"] = True
                    if kind == "waiting":
                        rec["status"] = "waiting"
                        rec["detail"] = "Cloud machine provisioning"
                    if kind == "assistant_block" and rec["first_output_s"] is None:
                        rec["first_output_s"] = round(time.monotonic() - t0, 2)
                        # The model spoke: the substrate is up, so hand the turn
                        # its real ceiling and retire the cold-start fuse.
                        if fuse_deadline is not None:
                            fuse_deadline = None
                            turn_deadline.reschedule(None)
                    if kind == "error":
                        rec["status"] = "error"
                        rec["detail"] = str(frame.get("message", ""))[:200]
                        if frame.get("code") in HOST_SCOPED_CODES:
                            # The chat layer already recorded it against the
                            # machine and may have moved the topic; don't undo
                            # that below just because the stream ended cleanly.
                            host_failed = True
                            self._host_failed_topics.add(str(topic_id))
                    await self._broker.publish(channel, frame)
            if rec["status"] == "running":
                rec["status"] = "done"
            rec["duration_s"] = round(time.monotonic() - t0, 1)
            if (
                rec["status"] == "done"
                and rec["first_output_s"] is None
                and summon
                and not is_resume
                and content.strip()
            ):
                # A summoned turn that published nothing. Usually 芝士 simply had
                # nothing to say; sometimes its platform tools were gone and it
                # answered into a terminal nobody reads (observed 2026-09-13/14:
                # `chat_send` returning `No such tool available` while the
                # session reported ready). Only this case pays for the question,
                # and only a confirmed reconnect re-delivers the message.
                await self._recover_silent_turn(
                    chat_service, topic_id, content, continuation_id
                )
            if not host_failed and str(topic_id) in self._host_failed_topics:
                self._host_failed_topics.discard(str(topic_id))
                # Streaming a turn to its end is the machine working. That breaks
                # the failure streak and lifts any quarantine (#186) — the way a
                # machine gets back into rotation without anyone clearing it.
                # Only reached when this process actually saw the machine fail:
                # there is nothing to clear otherwise, and paying a DB round-trip
                # on every good turn would delay the turn's release (see
                # `_host_failed_topics`).
                await record_host_success(topic_id=topic_id)
            logger.info(
                "turn done: status=%s tools=%s first_output=%ss duration=%ss",
                rec["status"],
                rec["tools"],
                rec["first_output_s"],
                rec["duration_s"],
            )
        except asyncio.CancelledError:
            # Killed from outside: `sweep_orphans` tearing down a wedged turn, or
            # the process shutting down. CancelledError is a BaseException, so
            # without this clause it escapes every handler below and the record
            # keeps saying `running` for as long as the process lives — and that
            # record is what `GET /topics` (`running`) and `/topics/{id}/status`
            # (`turn`) serve. Killing a turn while still reporting it alive is
            # the same lie the sweep exists to end, so the teardown has to close
            # the books here.
            rec["status"] = "cancelled"
            rec["duration_s"] = round(time.monotonic() - t0, 1)
            rec["detail"] = rec.get("detail") or "轮次被强制结束"
            logger.warning(
                "turn %s cancelled for topic %s after %ss",
                turn_id,
                topic_id,
                rec["duration_s"],
            )
            # Surface the request failure before `_run` publishes the explicit
            # turn_finished marker that retires this turn's broker state.
            # Publishing never suspends (it is queue writes only), so it is safe
            # on an already-cancelled task.
            await self._broker.publish(
                channel,
                {
                    "type": "error",
                    "message": "芝士这轮被强制结束了",
                    "persisted": False,
                },
            )
            # Ends the stream, for the reason spelled out on the timeout path.
            # `turn_finished` does not cover this: `_run` publishes it only when
            # `lifecycle["started"]` is set, and a turn the sweep tears down
            # before its first frame never announced itself, so a subscriber
            # would be left reading until its own timeout.
            await self._broker.publish(channel, {"type": "done"})
            # The on-disk registry entry is deliberately left alone: whoever
            # cancelled us owns it (the sweep already claimed it; a shutdown
            # wants startup to find and resume it).
            clear_context("turn", "topic")
            raise
        except TimeoutError:
            rec["status"] = "timeout"
            rec["duration_s"] = round(time.monotonic() - t0, 1)
            # The actual ceiling this turn ran against — `topic_work()` reads
            # the same `ceiling_s or self._timeout` fallback for `cheese
            # status` (see its docstring above); a backend that emitted a
            # `turn_ceiling` frame may have raised this well above
            # `self._timeout`, so logging the base default here would be
            # misleading about what actually elapsed before the cut.
            # Two different failures share this handler, and telling them apart is
            # the whole point of the cold-start fuse — it decides which message
            # the room gets. "Ran a long time and wedged" is a turn problem, and
            # its work so far is on disk. "Never produced a token" is an
            # ENVIRONMENT problem (no container, no disk, no model connection):
            # nothing ran, so saying 已完成的改动都在 there would be a lie.
            # Neither is retried — a person picks it up (a wedged turn re-runs
            # into the same dead machine, and an environment that never came up
            # needs someone to look).
            # Only the cold-start fuse raises this now: the ceiling is recorded
            # rather than scheduled, so a turn that produced output has no
            # deadline left to hit. Kept as a check rather than assumed, so a
            # future deadline added above cannot silently borrow the fuse's
            # wording.
            never_started = bool(
                rec["first_output_s"] is None and self._first_output_timeout_s
            )
            if not never_started:
                raise
            # Meta for the posted event: only the credential-expired case carries a
            # platform_error classification the frontend can render; the two generic
            # branches stay a plain system event, exactly as before.
            fuse_meta: dict | None = None
            # 平台提示统一契约的 meta，给「一个字没输出」和「超时」这两条用。
            # 凭据已过期那条走 `fuse_meta`（它带 code）。
            timeout_meta: dict | None = None
            if never_started and credential_expired:
                # #388 缺陷一: the backend KNEW the credential was dead. Say so —
                # the guessing message ("容器/磁盘/网络") is the one that cost four
                # people ten hours when the true reason was already known.
                logger.error(
                    "turn %s never produced output for topic %s and its model "
                    "credential is known-expired; fast-failing as a subscription "
                    "credential failure (tools=%s)",
                    turn_id,
                    topic_id,
                    rec["tools"],
                )
                rec["detail"] = "subscription credential expired"
                text = SUBSCRIPTION_CREDENTIAL_EXPIRED.content
                fuse_meta = SUBSCRIPTION_CREDENTIAL_EXPIRED.meta
            else:
                logger.error(
                    "turn %s produced no output within %ss for topic %s; "
                    "treating as a substrate failure (tools=%s)",
                    turn_id,
                    round(self._first_output_timeout_s),
                    topic_id,
                    rec["tools"],
                )
                rec["detail"] = "no first output"
                # 平台提示统一契约: 房间里一行，「常见原因」那一串进 meta.detail。
                text = (
                    f"芝士这轮一个字都没输出"
                    f"（{round(self._first_output_timeout_s)}秒），平台不会自动重试。"
                )
                timeout_meta = notice(
                    EVENT_TURN_TIMEOUT,
                    severity=SEVERITY_WARN,
                    # 运行环境没起来，平台不再自动重试 —— 要有人看一眼。
                    who=WHO_HUMAN,
                    detail=(
                        f"{round(self._first_output_timeout_s)}秒内没有任何模型输出，"
                        "也没有任何工具调用，按运行环境没起来处理。"
                        "常见原因：平台的模型订阅凭据过期（需要主机侧重新认证）、"
                        "沙箱容器建不起来、磁盘满了、或者模型侧连不上"
                        "——不是芝士卡在某一步，所以这里没有「已完成的改动」。"
                        "平台不会自动重试（重试只会再撞上同一个没起来的环境）；"
                        "需要有人看一眼运行环境（容器、磁盘、模型通路），"
                        "修好后 @ 芝士。"
                    ),
                    detail_label="常见原因",
                )
            block = None
            try:
                # `fuse_meta` (订阅凭据已过期) 优先：它带 code，下面的 error_frame
                # 认这个字段。其余两条走 `timeout_meta`。
                block = await chat_service.post_system_event(
                    topic_id, text, turn_id, meta=fuse_meta or timeout_meta
                )
            except Exception:  # noqa: BLE001 — best effort
                logger.exception("failed to persist timeout event")
            if block is not None:
                await self._broker.publish(
                    channel, {"type": "event_block", "block": block}
                )
            error_frame: dict = {
                "type": "error",
                "message": text,
                "persisted": block is not None,
            }
            if fuse_meta is not None:
                error_frame["code"] = SUBSCRIPTION_CREDENTIAL_EXPIRED.code
            await self._broker.publish(channel, error_frame)
            # End the stream. `chat` publishes `done` on the paths it owns, and
            # this one cut its generator off mid-flight, so without this nothing
            # does: a subscriber waiting for the turn to end instead waits out
            # its own read timeout, which is how a squeezed ceiling became a
            # 300-second hang in CI rather than a fast failure. `turn_finished`
            # is no substitute, being published only for a turn that got far
            # enough to announce itself, which a turn cut down during setup
            # never did.
            await self._broker.publish(channel, {"type": "done"})
            # No auto-retry, whatever the timeout was. A credential-dead turn
            # only burns the fuse again against the same expired credential
            # (#388's "对自己的失败没有记忆") and self-heals on the next human
            # summon once the host re-auths; a wedged one re-enters the machine
            # that just died; an environment that never came up needs a person.
            # All three now wait for someone to look — the event above said so.
        except AppError as exc:
            rec["status"] = "error"
            rec["detail"] = exc.message
            rec["duration_s"] = round(time.monotonic() - t0, 1)
            await self._broker.publish(
                channel, {"type": "error", "message": exc.message}
            )
            # Ends the stream, for the reason spelled out on the timeout path.
            await self._broker.publish(channel, {"type": "done"})
        except Exception as exc:  # noqa: BLE001 — surface runtime failures (spec H4)
            rec["status"] = "crashed"
            rec["duration_s"] = round(time.monotonic() - t0, 1)
            logger.exception("turn %s failed for topic %s", turn_id, topic_id)
            # The failure goes into the 现场 timeline as a persisted system event
            # (scrolls with the flow, survives reload) — not just a transient
            # banner. No invented cause: the log has the real traceback.
            platform_failure = classify_platform_failure(exc)
            if platform_failure is not None:
                text = platform_failure.content
                event_meta = platform_failure.meta
            else:
                # 平台提示统一契约: 一行给房间，别的收进 detail。真正的 traceback
                # 只进日志（这里连异常文本都不外发是刻意的 —— 见上面那段注释）。
                # 平台认不出来的失败当作缺陷信号，不当瞬时故障 —— 重试只会把同一个
                # bug 再触发一遍（2026-09-04 一个 NotFoundError 被连着自动重跑，
                # 把一个正在进行的 hackathon 房间刷了屏）。发一次，交给人。
                text = "芝士这轮中断了，平台不会自动重试"
                event_meta = notice(
                    EVENT_TURN_FAILED,
                    severity=SEVERITY_ERROR,
                    who=WHO_HUMAN,
                    detail=(
                        "已完成的改动都在；平台不会自动重试（这类失败多半是缺陷，"
                        "重试只会再触发一遍）。需要有人看一眼日志定位问题，"
                        "修好后重新 @ 芝士，它会从断点接着做。"
                    ),
                    detail_label="详细说明",
                )
            block = None
            try:
                block = await chat_service.post_system_event(
                    topic_id, text, turn_id, meta=event_meta
                )
            except Exception:  # noqa: BLE001 — best effort, never mask the error
                logger.exception("failed to persist turn-failure event")
            if block is not None:
                await self._broker.publish(
                    channel, {"type": "event_block", "block": block}
                )
            error_frame = {
                "type": "error",
                "message": text,
                "persisted": block is not None,
            }
            if platform_failure is not None:
                error_frame["code"] = platform_failure.code
            await self._broker.publish(channel, error_frame)
            # Ends the stream, for the reason spelled out on the timeout path.
            await self._broker.publish(channel, {"type": "done"})
            # An unnamed failure is a bug signal, not a transience signal, so the
            # platform does not repeat it — the event above already handed the
            # topic to a person (#574, and the 2026-09-04 room flood). A recovery
            # that survives a deploy is a different thing entirely and still runs:
            # recover/replay/adopt carry a live turn across a restart untouched.
            if platform_failure is not None and platform_failure.host_scoped:
                # The machine, not the turn, is the suspect. Account for it
                # against the device and, once it has failed twice in a row,
                # say so in the room by name. No retry and no other machine:
                # the topic stays pinned where it failed until a person has
                # looked (host_failure.py says why the swap it used to do is
                # gone).
                host_failed = True
                self._host_failed_topics.add(str(topic_id))
                verdict = await handle_host_failure(
                    topic_id=topic_id,
                    failure=platform_failure,
                )
                if verdict.message:
                    await self._post_event(
                        chat_service,
                        topic_id,
                        turn_id,
                        verdict.message,
                        meta=verdict.event_meta,
                    )
        # The interval ends here. Closing, not deleting: this turn's id is on
        # every block it produced, and an interval erased at its end is one
        # nobody can ask about afterwards.
        #
        # Failing to close is survivable and must not be reported as the turn
        # failing — the turn is over and its work landed. What is left open gets
        # picked up by the next sweep, which sees a delivered prompt and
        # attaches rather than re-sending it.
        try:
            await _close_turns(chat_service.session_factory, [turn_id])
        except Exception:  # noqa: BLE001 — the turn already finished
            logger.exception("could not close the interval for turn %s", turn_id)
        clear_context("turn", "topic")
