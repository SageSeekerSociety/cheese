"""TurnRunner + Broker: run agent turns as background jobs; WS is a subscriber.

Design §4 / v2 R1·R3. The WebSocket used to run the turn inside its own
coroutine, so a disconnect tore down the work. Here a TurnRunner runs the turn as
a background task and publishes its frames to a Broker; WebSocket connections just
SUBSCRIBE and relay. A disconnect only drops the subscription — the turn keeps
running and persisting (invariant 2: the job does not depend on who is watching).

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
from datetime import datetime
from functools import lru_cache

from app.core.errors import AppError
from app.core.obs import bind_context, clear_context
from app.domain.agent.platform_failures import classify_platform_failure

logger = logging.getLogger("cheesex.runtime")


def _inflight_path():
    from pathlib import Path

    from app.core.config import settings

    return Path(settings.workspace_root) / ".turns-inflight.json"


def _load_inflight() -> dict:
    import json

    try:
        return json.loads(_inflight_path().read_text())
    except Exception:  # noqa: BLE001 — missing/corrupt file = empty registry
        return {}


def _save_inflight(reg: dict) -> None:
    import json

    try:
        path = _inflight_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(reg))
    except Exception:  # noqa: BLE001 — registry is best-effort
        logger.exception("failed to persist in-flight turn registry")


def _continuation_of(turn_id: str, info: dict) -> uuid.UUID:
    """The continuation an orphaned registry entry should resume under.

    Written by a current backend, the entry carries one. A LEGACY entry (written
    before the field existed) does not, and its turn id is the right fallback:
    "the continuation of a first attempt IS its turn id" is the invariant
    ``_execute`` maintains.

    Anything unparseable gets a fresh id rather than an exception. That means no
    key from the dead turn will match — the resume degrades to the old
    duplicate-side-effect risk for that ONE turn — but the alternative is
    raising out of the orphan sweep, which would strand every OTHER turn the
    sweep exists to rescue. A narrower failure beats a louder one here."""
    raw = info.get("continuation_id")
    for candidate in (raw, turn_id):
        if isinstance(candidate, str):
            try:
                return uuid.UUID(candidate)
            except ValueError:
                continue
    logger.warning("orphan entry %s has no usable continuation id", turn_id)
    return uuid.uuid4()


# Channel = the topic id (str). Frames are the same dicts converse yields.
Frame = dict


class InProcessBroker:
    """Fan-out pub/sub for one process, with a per-channel replay buffer of the
    IN-PROGRESS turn's ephemeral frames (R3). A connection that subscribes mid-turn
    gets those frames immediately (catch-up), then the live continuation — so a
    reconnect (after `GET /blocks` for persisted history) is seamless. The buffer
    is dropped when the turn ends (done/error), since its result is now persisted
    as blocks; between turns the buffer is empty, so a fresh submit replays nothing.
    """

    def __init__(self, replay_size: int = 512) -> None:
        self._subs: dict[str, set[asyncio.Queue[Frame]]] = {}
        self._buffer: dict[str, list[Frame]] = {}
        self._replay_size = replay_size

    def reset(self) -> None:
        """Drop all buffered frames + subscriptions. The broker is a process-wide
        singleton (get_broker is lru_cached); tests that TRUNCATE ... RESTART
        IDENTITY reuse channel ids (topic id 1, 2, …) across tests, so without this
        a prior test's buffered frames would replay into the next test on the same
        reused channel. Called between tests by the client/python_client fixtures."""
        self._subs.clear()
        self._buffer.clear()

    async def publish(self, channel: str, frame: Frame) -> None:
        # Reaction frames are standalone state updates, not turn progress: they
        # can fire on an idle channel (a human reacting between turns) and are
        # rebuilt from GET /blocks on (re)connect — so they are fanned out live
        # but never buffered (buffering would also make an idle channel look
        # in_flight forever).
        if frame.get("type") == "reaction":
            for q in list(self._subs.get(channel, ())):
                q.put_nowait(frame)
            return
        buf = self._buffer.setdefault(channel, [])
        buf.append(frame)
        if frame.get("type") in ("done", "error"):
            # Turn finished — its output is persisted as blocks now; drop the
            # in-progress buffer so a later subscriber doesn't replay a dead turn.
            self._buffer.pop(channel, None)
        elif len(buf) > self._replay_size:
            del buf[: len(buf) - self._replay_size]
        for q in list(self._subs.get(channel, ())):
            q.put_nowait(frame)

    def in_flight(self, channel: str) -> bool:
        """True while a turn is mid-stream on this channel: the replay buffer
        holds frames from turn start until its done/error clears it. Lets a
        (re)connecting client rebuild the 正在思考 indicator instead of showing
        a silent, seemingly-dead topic."""
        return bool(self._buffer.get(channel))

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


class TurnRunner:
    """Runs a converse turn as a background task and publishes its frames to the
    broker. The turn owns its lifecycle; subscribers come and go.

    The runner is a process singleton (its task registry must outlive any single
    connection), so the ChatService is passed per-submit rather than held — that
    keeps it resolved through FastAPI's dependency overrides (e.g. tests)."""

    def __init__(
        self, broker: InProcessBroker, *, turn_timeout_s: float = 900.0
    ) -> None:
        self._broker = broker
        self._timeout = turn_timeout_s
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
        # back off the on-disk registry because `live_turn_for_topic` answers a
        # request (`/topics/{id}/status`), and that must not cost a file read.
        self._live_topics: dict[str, uuid.UUID] = {}

    def recent_turns(self) -> list[dict]:
        """Newest-first lifecycle summaries for /debug/turns."""
        return list(reversed(self._recent))

    def active_turns(self) -> int:
        """How many turns are currently in flight — /health exposes this so a
        redeploy can drain (wait for running turns) instead of killing them."""
        return len(self._tasks)

    def topic_turn(self, topic_id: uuid.UUID) -> dict | None:
        """Latest lifecycle record for this topic. `ceiling_s` is this turn's
        effective absolute ceiling (`self._timeout` for most backends; the tmux
        backend's own hard ceiling once its `turn_ceiling` frame has rescheduled
        the outer wrap — see `_execute`) and `near_ceiling` is a coarse "within
        the last 10 minutes" flag — turn 活跃度检测 deliberately does NOT expose a
        live `budget_left_s` countdown any more: that figure was observed making
        the agent rush against what's only meant to be a wedged-turn safety net
        (dev, 2026-08-08). The idle-suspect layer (tmux only) isn't tracked here
        — see `ChatService.tmux_activity_status` / `/topics/{id}/status`.
        Ring-buffer-backed, so None after a restart or ~100 turns elsewhere."""
        key = str(topic_id)
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
        key = str(topic_id)
        for rec in reversed(self._recent):
            if rec["topic_id"] != key:
                continue
            if rec["status"] != "running":
                return None
            raw = rec.get("continuation_id")
            return uuid.UUID(raw) if isinstance(raw, str) else None
        return None

    def running_topic_ids(self) -> set[uuid.UUID]:
        """Every topic with a turn currently in flight — for bulk UI signals
        (e.g. the sidebar's "还在说话" indicator) that can't afford one
        `topic_turn()` lookup per row. Same "newest record per topic wins"
        rule as `topic_turn()`, just collected across all topics at once."""
        seen: set[str] = set()
        running: set[uuid.UUID] = set()
        for rec in reversed(self._recent):
            key = rec["topic_id"]
            if key in seen:
                continue
            seen.add(key)
            if rec["status"] == "running":
                running.add(uuid.UUID(key))
        return running

    def live_turn_for_topic(self, topic_id: uuid.UUID) -> dict | None:
        """The turn THIS process is actually executing for `topic_id`, with how
        long since it last published a frame — or None if nobody is running one.

        This is the heartbeat half of the stall verdict (see
        `TopicService.stall_signal`), and deliberately not `topic_turn()`:
        `_recent` is a ring buffer of what turns *did*, so a turn killed with the
        process still reads `running` there forever. `_live` is emptied by the
        turn's own `finally`, which a dying process never gets to run — so a
        registry entry with no `_live` entry means the executor is gone, no
        matter what the buffer remembers.
        """
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
        continuation_id: uuid.UUID | None = None,
    ) -> uuid.UUID:
        """Start a turn in the background; return its turn_id immediately. Turns
        on the same topic serialize on ChatService's per-topic lock (so a second
        submit queues behind the first).

        ``continuation_id`` names the logical unit of work. A fresh turn starts
        one (defaulting to its own turn id); an auto-resume INHERITS the
        interrupted turn's, which is what lets a side effect the first attempt
        already performed be recognised as done — see domain.idempotency.keys."""
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
                continuation_id=continuation_id or turn_id,
            )
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return turn_id

    def submit_kickoff(
        self, chat_service, topic_id: uuid.UUID, *, prompt: str | None = None
    ) -> uuid.UUID:
        """A platform-event turn (spec §8.4): 分身自动开工 after a split/upgrade
        (default prompt), or the parent digesting a returned conclusion (custom
        prompt). No human message is posted — the agent speaks for itself; the
        pre-built kickoff frame stream rides the same _run pipeline (telemetry,
        timeout, failure events) via the `frames` override."""
        turn_id = uuid.uuid4()
        frames = chat_service.kickoff(topic_id=topic_id, turn_id=turn_id, prompt=prompt)
        task = asyncio.create_task(
            self._run(
                chat_service,
                topic_id,
                turn_id,
                author="system",
                content=prompt or "",
                summon=True,
                frames=frames,
            )
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return turn_id

    # What the auto-resumed turn asks 芝士 to do.
    #
    # The old wording asserted "你的工作区和已完成的进度都在" unconditionally.
    # That claim is only true of FILES. Whether the conversation came back
    # depends on the session pointer having reached the DB before the process
    # died, and for a pure investigation turn — which produces no files at all —
    # "进度都在" can be false in every sense (2026-08-11, this topic: a
    # 132-message turn resumed into a blank session that had to reconstruct the
    # task from the blocks API). Telling a context-less 芝士 that its progress is
    # intact is exactly how it redoes work it cannot see.
    #
    # So: promise only the part that is always true, and say plainly that the
    # rest has to be checked rather than assumed.
    RESUME_PROMPT = (
        "上一轮在中途断了（原因见上一条系统事件）。工作区里的文件都在，"
        "但**对话上下文不保证接上了**——如果你对上一轮做过什么没有印象，"
        "那就是没接上：先核对已经发生的事（jj status 看改动、翻本话题的消息记录"
        "看已经说过和做过什么），再决定从哪继续，别凭猜重做。"
        "确认原任务其实已完成的话，直接收尾汇报。"
    )

    # Past this age an orphan is not auto-resumed: continuing a conversation
    # from hours ago is usually not what anyone still wants. Do NOT reach for
    # this number when an orphan goes unnoticed — the bug was never where the
    # line sits, it was that crossing it did nothing at all. Raising it only
    # makes the silence last longer.
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

    async def _wedged_turns(
        self,
        reg: dict,
        last_activity: (
            Callable[[set[uuid.UUID]], Awaitable[dict[uuid.UUID, datetime]]] | None
        ),
        silence_s: float,
        now: float,
    ) -> set[str]:
        """Of the turns this process believes it is running, which have gone
        quiet on BOTH signals? Startup passes no `last_activity` — there is
        nothing in `_live` to judge then, so the probe is skipped entirely."""
        candidates = {tid for tid in reg if tid in self._live}
        if not candidates or last_activity is None:
            return set()
        topics = {uuid.UUID(reg[tid]["topic_id"]) for tid in candidates}
        try:
            blocks_at = await last_activity(topics)
        except Exception:  # noqa: BLE001 — a failed probe must not cancel turns
            logger.exception("orphan sweep: last-activity probe failed")
            return set()
        mono = time.monotonic()
        wedged: set[str] = set()
        for tid in candidates:
            info = reg[tid]
            # Wall-clock age of the newest Block in the topic...
            last_block = blocks_at.get(uuid.UUID(info["topic_id"]))
            block_quiet_s = (
                (now - last_block.timestamp())
                if last_block is not None
                else (now - float(info.get("started_at", 0)))
            )
            # ...versus the newest frame this process published for this turn.
            # A turn we are running always has an entry (set at start), so a
            # missing one means the bookkeeping is off — treat it as fresh and
            # let the not-in-`_live` branch handle it instead of guessing.
            frame_at = self._last_frame_at.get(tid)
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
        """Startup sweep. `_live` is empty at boot, so every registry entry is
        by definition an orphan of the previous process generation — which makes
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
        """Claim every registered turn that is not actually progressing, and make
        its fate VISIBLE in the topic. Returns how many were auto-resumed.

        Why this is not startup-only (the 101/173-minute incident, 2026-08-11):
        a turn dying does not imply the platform restarted. A container recreate,
        an OOM-killed child, a sandbox image swap — each kills a turn while the
        process lives happily on. Nothing then ever re-reads the registry, so the
        entry sits there and the topic keeps reporting `active` with a last block
        that is a command which never returned. That is indistinguishable, to a
        human reading the platform, from a slow test run.

        A turn is dead in one of two ways, and BOTH must be caught — the second
        is what let three topics lie silent for 8 hours on 2026-08-11 while this
        process was up the whole time:

        1. Not in `_live` — a previous generation of the process started it and
           died. The registry outlives the process; `_live` does not.
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

        Every branch below ends in a system event. A turn we do not resume is a
        turn someone has to pick up by hand, and they can only do that if the
        topic says so — silence is the failure mode, not the loud recovery."""
        import time as _time

        reg = _load_inflight()
        if not reg:
            return 0
        if min_age_s is None:
            min_age_s = self.SWEEP_MIN_AGE_S
        if silence_s is None:
            silence_s = self.SILENT_TURN_S
        now = _time.time()
        old_enough = {
            tid: info
            for tid, info in reg.items()
            if now - float(info.get("started_at", 0)) >= min_age_s
        }
        wedged = await self._wedged_turns(old_enough, last_activity, silence_s, now)
        orphans = {
            tid: info
            for tid, info in old_enough.items()
            if tid not in self._live or tid in wedged
        }
        if not orphans:
            return 0
        # Keep what we did not claim (live turns, entries too young to judge);
        # their own completion path removes them. Re-read rather than writing
        # back the snapshot from the top of this method: the activity probe
        # awaited, and a turn that registered during that window is in the file
        # but not in `reg`. Writing the stale copy would delete its entry — and
        # a running turn with no registry entry is invisible to every future
        # sweep, i.e. the next death is silent again, which is the whole bug.
        surviving = _load_inflight()
        _save_inflight({k: v for k, v in surviving.items() if k not in orphans})
        resumed = 0
        for turn_id, info in orphans.items():
            topic_id = uuid.UUID(info["topic_id"])
            age_s = now - float(info.get("started_at", 0))
            stale = age_s > self.ORPHAN_STALE_S
            chained = bool(info.get("is_resume"))
            # A wedged turn still owns the topic lock. Cancelling is not tidiness
            # — a resume would queue behind it forever, and even a turn we refuse
            # to resume must let the next human message through.
            if turn_id in wedged:
                self._cancel_wedged(turn_id, topic_id)
            # Same verdict either way, but say which one actually happened —
            # "it was killed" and "it sat there producing nothing" send whoever
            # reads this to different places.
            how = (
                f"卡死了：{round(age_s / 60)} 分钟里一个字都没输出，已强制结束"
                if turn_id in wedged
                else "被强制中断了（进程或沙箱被杀，没有走到收尾）"
            )
            if stale or chained:
                # The two "we are NOT resuming this" branches. They used to be a
                # bare `continue`, which is what let a dead topic look identical
                # to a working one for 173 minutes.
                why = (
                    f"已经中断 {round(age_s / 60)} 分钟了，太久，不自动接着跑"
                    if stale
                    else "这轮本身就是一次自动续跑，不再连着自动续跑"
                )
                await self._post_orphan_event(
                    chat_service,
                    topic_id,
                    f"⚠️ 芝士上一轮{how}，{why}。"
                    "已完成的改动都还在工作区里 —— 需要继续的话 @ 芝士，"
                    "它会从断点接着做。",
                )
                logger.info(
                    "orphan turn %s dropped (stale=%s chained=%s, age=%ss)",
                    turn_id,
                    stale,
                    chained,
                    round(age_s),
                )
                continue
            await self._post_orphan_event(
                chat_service,
                topic_id,
                f"⚠️ 上一轮{how}。已完成的进度都在；马上自动接着跑。",
            )
            # A cancelled turn needs a moment to unwind before it lets go of the
            # topic lock; a dead process holds no lock at all. The resume would
            # queue rather than fail either way — this just avoids the queue.
            self._schedule_resume(
                chat_service,
                topic_id,
                10.0 if turn_id in wedged else 3.0,
                "上一轮被强制中断，接着跑",
                continuation_id=_continuation_of(turn_id, info),
            )
            resumed += 1
            logger.info("orphan turn %s scheduled for resume", turn_id)
        return resumed

    async def _post_orphan_event(
        self, chat_service, topic_id: uuid.UUID, text: str
    ) -> None:
        """Persist + broadcast an orphan verdict. Best-effort by design: for a
        resumed orphan the resume matters more than the notice, and for a dropped
        one there is nothing left to fail into."""
        try:
            block = await chat_service.post_system_event(topic_id, text)
            if block is not None:
                await self._broker.publish(
                    str(topic_id), {"type": "event_block", "block": block}
                )
        except Exception:  # noqa: BLE001 — a notice must never break the sweep
            logger.exception("orphan event failed for %s", topic_id)

    def _schedule_resume(
        self,
        chat_service,
        topic_id: uuid.UUID,
        after_s: float,
        reason: str = "从上一轮的断点继续",
        *,
        continuation_id: uuid.UUID | None = None,
    ):
        """One bounded auto-resume: wait, then run a system-nudged turn that
        continues the saved session. Resumed turns never schedule another
        resume (is_resume=True), so a persistent failure stops after one shot.

        The resume runs under the interrupted turn's ``continuation_id``, so any
        side effect the first attempt already committed is recognised as done
        rather than performed twice."""

        async def _later() -> None:
            await asyncio.sleep(after_s)
            self.submit(
                chat_service,
                topic_id,
                author="system",
                content=self.RESUME_PROMPT,
                summon=True,
                is_resume=True,
                resume_reason=reason,
                continuation_id=continuation_id,
            )

        task = asyncio.create_task(_later())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        logger.info("scheduled auto-resume for topic %s in %.0fs", topic_id, after_s)

    # Platform copy for the queue event — structured, never 芝士's own words.
    @staticmethod
    def _queued_text(ahead: int) -> str:
        if ahead <= 0:
            return "⏳ 项目同时进行的轮次已满，这轮先排队，等前面的轮次结束就开跑。"
        return f"⏳ 项目同时进行的轮次已满，这轮先排队，前面还有 {ahead} 个在等。"

    async def _post_event(
        self, chat_service, topic_id: uuid.UUID, turn_id: uuid.UUID, text: str
    ) -> bool:
        """Persist + broadcast a platform system event (queue/refusal). Reuses
        the post_system_event + broker path the nudge mechanism uses."""
        try:
            block = await chat_service.post_system_event(topic_id, text, turn_id)
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
            policy = await chat_service.turn_policy(topic_id)
        except Exception:  # noqa: BLE001 — admission must never kill a turn
            logger.exception("turn_policy failed for %s; admitting", topic_id)
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
                chat_service, topic_id, turn_id, self._queued_text(ahead)
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
    ) -> None:
        """Refuse a turn for exhausted credits. A human's message still lands
        (speaking is free — only the AI turn is metered): it goes through a
        summon=False converse pass, then the structured platform event says why
        芝士 isn't coming. The copy is the PLATFORM's, never the model's."""
        from app.domain.usage.credits import CREDITS_EXHAUSTED_EVENT

        channel = str(topic_id)
        if is_message_turn and (content or attachments):
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
            chat_service, topic_id, turn_id, CREDITS_EXHAUSTED_EVENT
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
        continuation_id: uuid.UUID | None = None,
        # Pre-built frame stream (kickoff turns). None → run a converse turn.
        frames: AsyncIterator[Frame] | None = None,
    ) -> None:
        # 算力闸 (spec §9.1): refuse on exhausted credits, queue when the
        # project's concurrent-turn ceiling is reached. Both states are posted
        # into the topic as platform system events, so people SEE why nothing
        # is streaming yet.
        verdict, gate = await self._admit(chat_service, topic_id, turn_id)
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
            )
            return
        try:
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
                continuation_id=continuation_id,
                frames=frames,
            )
        finally:
            # Drop the liveness mark here, not in `_execute`: a turn killed by
            # task cancellation (CancelledError is a BaseException — it misses
            # every `except` inside `_execute`, including the registry cleanup)
            # must stop counting as live, so the next sweep can claim it. The
            # on-disk entry deliberately survives — that is what gets it resumed.
            self._live.pop(str(turn_id), None)
            self._last_frame_at.pop(str(turn_id), None)
            self._live_topics.pop(str(turn_id), None)
            if gate is not None:
                gate.release()

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
        continuation_id: uuid.UUID | None = None,
        # Pre-built frame stream (kickoff turns). None → run a converse turn.
        frames: AsyncIterator[Frame] | None = None,
    ) -> None:
        channel = str(topic_id)
        continuation_id = continuation_id or turn_id
        resume_after: float | None = None
        resume_why = "上一轮异常中断，接着跑"
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
        # Durable in-flight registry: if the PROCESS dies (deploy past the drain
        # ceiling, crash), startup finds the orphan and auto-resumes it — a
        # killed turn must never just vanish.
        reg = _load_inflight()
        reg[str(turn_id)] = {
            "topic_id": str(topic_id),
            "started_at": rec["started_at"],
            "is_resume": is_resume,
            # Carried across the process death: the orphan sweep must resume
            # under the SAME continuation, or every key the dead turn claimed
            # stops matching and its side effects are all repeated.
            "continuation_id": str(continuation_id),
        }
        _save_inflight(reg)
        # Same instant, no await in between: a sweep can never observe this turn
        # on disk but not in `_live` and mistake a just-started turn for a corpse.
        current = asyncio.current_task()
        if current is not None:
            self._live[str(turn_id)] = current
        self._last_frame_at[str(turn_id)] = time.monotonic()
        self._live_topics[str(turn_id)] = topic_id
        logger.info(
            "turn start: author=%s summon=%s resume=%s", author, summon, is_resume
        )
        try:
            # Wall-clock ceiling (R8): a wedged turn must not hold the topic lock
            # forever. On timeout the async-for exits, closing the converse
            # generator → its `async with` blocks unwind → the topic lock releases
            # and the in-container claude process is torn down.
            #
            # This wrap is transport-INDEPENDENT — one TurnRunner singleton, same
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
            async with asyncio.timeout(self._timeout) as turn_deadline:
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
                        continuation_id=continuation_id,
                    )
                )
                async for frame in turn_frames:
                    kind = frame.get("type")
                    # Proof of life for the silence check in sweep_orphans, taken
                    # before the `continue`s below so EVERY frame counts. A tool
                    # call persists no Block, so without this a turn legitimately
                    # grinding through tools looks identical to a wedged one.
                    self._last_frame_at[str(turn_id)] = time.monotonic()
                    if kind == "turn_ceiling":
                        ceiling_s = float(frame.get("seconds", self._timeout))
                        turn_deadline.reschedule(loop_start + max(0.0, ceiling_s))
                        # `topic_turn()` reads this so `cheese status` reports the
                        # backend's REAL ceiling, not the generic outer default.
                        rec["ceiling_s"] = ceiling_s
                        continue
                    if kind == "resume_hint":
                        # Internal: chat layer says this failure is worth an
                        # automatic continuation (e.g. rate-limit reset time).
                        resume_after = float(frame.get("after_s", 5))
                        resume_why = str(frame.get("reason") or resume_why)
                        rec["detail"] = resume_why
                        continue
                    if (
                        kind in ("tool", "assistant_block")
                        and rec["first_output_s"] is None
                    ):
                        rec["first_output_s"] = round(time.monotonic() - t0, 2)
                    if kind == "tool":
                        rec["tools"] += 1
                    if kind == "error":
                        rec["status"] = "error"
                        rec["detail"] = str(frame.get("message", ""))[:200]
                    await self._broker.publish(channel, frame)
            if rec["status"] == "running":
                rec["status"] = "done"
            rec["duration_s"] = round(time.monotonic() - t0, 1)
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
            # An `error` frame is also what drops the broker's replay buffer, so
            # a client reconnecting after the kill stops being told the dead turn
            # is still streaming. Publishing never suspends (it is queue writes
            # only), so it is safe on an already-cancelled task.
            await self._broker.publish(
                channel,
                {
                    "type": "error",
                    "message": "⚠️ 芝士这轮被强制结束了（详情见话题里的系统事件）。",
                    "persisted": False,
                },
            )
            # The on-disk registry entry is deliberately left alone: whoever
            # cancelled us owns it (the sweep already claimed it; a shutdown
            # wants startup to find and resume it).
            clear_context("turn", "topic")
            raise
        except TimeoutError:
            rec["status"] = "timeout"
            rec["duration_s"] = round(time.monotonic() - t0, 1)
            # The actual ceiling this turn ran against — `topic_turn()` reads
            # the same `ceiling_s or self._timeout` fallback for `cheese
            # status` (see its docstring above); a backend that emitted a
            # `turn_ceiling` frame may have raised this well above
            # `self._timeout`, so logging the base default here would be
            # misleading about what actually elapsed before the cut.
            effective_ceiling_s = round(rec.get("ceiling_s") or self._timeout)
            logger.warning(
                "turn %s timed out (>%ss, elapsed %ss) for topic %s; interrupted",
                turn_id,
                effective_ceiling_s,
                rec["duration_s"],
                topic_id,
            )
            text = (
                f"⚠️ 芝士这轮超时被中断了（{effective_ceiling_s}秒的上限，"
                f"实际跑了约{rec['duration_s']}秒，可能卡在某步）。"
                "已完成的改动都在；马上自动接着跑一次。"
            )
            block = None
            try:
                block = await chat_service.post_system_event(topic_id, text, turn_id)
            except Exception:  # noqa: BLE001 — best effort
                logger.exception("failed to persist timeout event")
            if block is not None:
                await self._broker.publish(
                    channel, {"type": "event_block", "block": block}
                )
            await self._broker.publish(
                channel,
                {"type": "error", "message": text, "persisted": block is not None},
            )
            if not is_resume:
                resume_after = 10.0
                resume_why = "上一轮超时中断，接着跑"
        except AppError as exc:
            rec["status"] = "error"
            rec["detail"] = exc.message
            rec["duration_s"] = round(time.monotonic() - t0, 1)
            await self._broker.publish(
                channel, {"type": "error", "message": exc.message}
            )
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
                text = (
                    "⚠️ 芝士这轮中断了。已完成的改动都在；马上自动接着跑一次，"
                    "若再失败就需要你再 @ 它。"
                )
                event_meta = None
            block = None
            try:
                if event_meta is None:
                    block = await chat_service.post_system_event(
                        topic_id, text, turn_id
                    )
                else:
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
            if not is_resume and platform_failure is None:
                resume_after = 5.0
        if resume_after is not None and not is_resume:
            rec["detail"] = f"{rec.get('detail') or ''} → 已排自动续跑({resume_why})"
            self._schedule_resume(
                chat_service,
                topic_id,
                resume_after,
                resume_why,
                continuation_id=continuation_id,
            )
        else:
            # 结论卡·阶段一 (机制①): this topic's turn ended and any conclusion
            # card it was handed is still open → 默认采信. THE place to put this
            # is here: transport-independent, so SDK/tmux/device backends all
            # get it. Skipped when a resume is queued — the continuation turn is
            # the one that will actually read the card. Best-effort: the 30-minute
            # sweeper is the backstop, and nothing here may break the turn.
            try:
                from datetime import UTC, datetime

                from app.domain.conclusion.services import settle_turn_cards

                settled = await settle_turn_cards(
                    chat_service.session_factory,
                    topic_id,
                    turn_started_at=datetime.fromtimestamp(rec["started_at"], UTC),
                )
                if settled:
                    logger.info(
                        "turn end: auto-accepted %d conclusion card(s)", settled
                    )
            except Exception:  # noqa: BLE001 — a turn must never fail on this
                logger.exception("conclusion settle failed for topic %s", topic_id)
        reg = _load_inflight()
        if reg.pop(str(turn_id), None) is not None:
            _save_inflight(reg)
        clear_context("turn", "topic")
