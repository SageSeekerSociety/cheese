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
from collections.abc import AsyncGenerator, AsyncIterator

from app.core.errors import AppError
from app.core.obs import bind_context, clear_context

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

    def recent_turns(self) -> list[dict]:
        """Newest-first lifecycle summaries for /debug/turns."""
        return list(reversed(self._recent))

    def active_turns(self) -> int:
        """How many turns are currently in flight — /health exposes this so a
        redeploy can drain (wait for running turns) instead of killing them."""
        return len(self._tasks)

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
    ) -> uuid.UUID:
        """Start a turn in the background; return its turn_id immediately. Turns
        on the same topic serialize on ChatService's per-topic lock (so a second
        submit queues behind the first)."""
        turn_id = uuid.uuid4()
        task = asyncio.create_task(
            self._run(
                chat_service, topic_id, turn_id,
                author=author, content=content, summon=summon, reply_to=reply_to,
                attachments=attachments, is_resume=is_resume,
                resume_reason=resume_reason, nudge_event=nudge_event,
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
        frames = chat_service.kickoff(
            topic_id=topic_id, turn_id=turn_id, prompt=prompt
        )
        task = asyncio.create_task(
            self._run(
                chat_service, topic_id, turn_id,
                author="system", content=prompt or "", summon=True,
                frames=frames,
            )
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return turn_id

    # What the auto-resumed turn asks 芝士 to do. Its progress is intact: the
    # topic's session pointer was saved on failure (resume, not replay).
    RESUME_PROMPT = (
        "上一轮在中途断了（原因见上一条系统事件）。你的工作区和已完成的进度都在，"
        "请从断点接着完成原任务；如果其实已经完成了，就直接收尾汇报。"
    )

    async def resume_orphans(self, chat_service) -> int:
        """Startup sweep: turns that were RUNNING when the previous process
        died (deploy past the drain ceiling, crash) get a ⚠️ event and one
        auto-resume — their sessions were checkpointed, so they continue
        instead of silently vanishing. Stale entries (>2h) are dropped."""
        import time as _time

        reg = _load_inflight()
        if not reg:
            return 0
        _save_inflight({})
        resumed = 0
        for turn_id, info in reg.items():
            if _time.time() - float(info.get("started_at", 0)) > 7200:
                continue
            if info.get("is_resume"):
                continue  # never chain resumes, even across restarts
            topic_id = uuid.UUID(info["topic_id"])
            try:
                block = await chat_service.post_system_event(
                    topic_id,
                    "⚠️ 上一轮在平台重启时被打断。已完成的进度都在；马上自动接着跑。",
                )
                if block is not None:
                    await self._broker.publish(
                        str(topic_id), {"type": "event_block", "block": block}
                    )
            except Exception:  # noqa: BLE001 — the resume matters more
                logger.exception("orphan event failed for %s", topic_id)
            self._schedule_resume(
                chat_service, topic_id, 3.0, "上一轮被平台重启打断，接着跑"
            )
            resumed += 1
            logger.info("orphan turn %s scheduled for resume", turn_id)
        return resumed

    def _schedule_resume(
        self,
        chat_service,
        topic_id: uuid.UUID,
        after_s: float,
        reason: str = "从上一轮的断点继续",
    ):
        """One bounded auto-resume: wait, then run a system-nudged turn that
        continues the saved session. Resumed turns never schedule another
        resume (is_resume=True), so a persistent failure stops after one shot."""

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
                chat_service, topic_id, turn_id,
                author=author, content=content, summon=summon,
                reply_to=reply_to, attachments=attachments,
                is_resume=is_resume, resume_reason=resume_reason,
                nudge_event=nudge_event, frames=frames,
            )
        finally:
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
        # Pre-built frame stream (kickoff turns). None → run a converse turn.
        frames: AsyncIterator[Frame] | None = None,
    ) -> None:
        channel = str(topic_id)
        resume_after: float | None = None
        resume_why = "上一轮异常中断，接着跑"
        # Correlate: every log line anywhere inside this turn carries these ids.
        bind_context(turn=str(turn_id)[:8], topic=str(topic_id)[:8])
        t0 = time.monotonic()
        rec = {
            "turn_id": str(turn_id),
            "topic_id": str(topic_id),
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
        }
        _save_inflight(reg)
        logger.info(
            "turn start: author=%s summon=%s resume=%s", author, summon, is_resume
        )
        try:
            # Wall-clock ceiling (R8): a wedged turn must not hold the topic lock
            # forever. On timeout the async-for exits, closing the converse
            # generator → its `async with` blocks unwind → the topic lock releases
            # and the in-container claude process is torn down.
            async with asyncio.timeout(self._timeout):
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
                    )
                )
                async for frame in turn_frames:
                    kind = frame.get("type")
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
                rec["status"], rec["tools"], rec["first_output_s"], rec["duration_s"],
            )
        except TimeoutError:
            rec["status"] = "timeout"
            rec["duration_s"] = round(time.monotonic() - t0, 1)
            logger.warning(
                "turn %s timed out (>%ss) for topic %s; interrupted",
                turn_id, self._timeout, topic_id,
            )
            text = (
                "⚠️ 芝士这轮超时被中断了（可能卡在某步）。已完成的改动都在；"
                "马上自动接着跑一次。"
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
        except Exception:  # noqa: BLE001 — surface agent/runtime failures (spec H4)
            rec["status"] = "crashed"
            rec["duration_s"] = round(time.monotonic() - t0, 1)
            logger.exception("turn %s failed for topic %s", turn_id, topic_id)
            # The failure goes into the 现场 timeline as a persisted system event
            # (scrolls with the flow, survives reload) — not just a transient
            # banner. No invented cause: the log has the real traceback.
            text = (
                "⚠️ 芝士这轮中断了。已完成的改动都在；马上自动接着跑一次，"
                "若再失败就需要你再 @ 它。"
            )
            block = None
            try:
                block = await chat_service.post_system_event(topic_id, text, turn_id)
            except Exception:  # noqa: BLE001 — best effort, never mask the error
                logger.exception("failed to persist turn-failure event")
            if block is not None:
                await self._broker.publish(
                    channel, {"type": "event_block", "block": block}
                )
            await self._broker.publish(
                channel,
                {"type": "error", "message": text, "persisted": block is not None},
            )
            if not is_resume:
                resume_after = 5.0
        if resume_after is not None and not is_resume:
            rec["detail"] = f"{rec.get('detail') or ''} → 已排自动续跑({resume_why})"
            self._schedule_resume(chat_service, topic_id, resume_after, resume_why)
        reg = _load_inflight()
        if reg.pop(str(turn_id), None) is not None:
            _save_inflight(reg)
        clear_context("turn", "topic")
