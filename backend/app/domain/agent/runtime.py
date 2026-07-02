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
import uuid
from collections.abc import AsyncIterator

from app.core.errors import AppError

logger = logging.getLogger("cheesex.runtime")

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

    def _schedule_resume(self, chat_service, topic_id: uuid.UUID, after_s: float):
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
            )

        task = asyncio.create_task(_later())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        logger.info("scheduled auto-resume for topic %s in %.0fs", topic_id, after_s)

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
    ) -> None:
        channel = str(topic_id)
        resume_after: float | None = None
        try:
            # Wall-clock ceiling (R8): a wedged turn must not hold the topic lock
            # forever. On timeout the async-for exits, closing the converse
            # generator → its `async with` blocks unwind → the topic lock releases
            # and the in-container claude process is torn down.
            async with asyncio.timeout(self._timeout):
                async for frame in chat_service.converse(
                    topic_id=topic_id,
                    author=author,
                    content=content,
                    summon=summon,
                    turn_id=turn_id,
                    reply_to=reply_to,
                    attachments=attachments,
                    is_resume=is_resume,
                ):
                    if frame.get("type") == "resume_hint":
                        # Internal: chat layer says this failure is worth an
                        # automatic continuation (e.g. rate-limit reset time).
                        resume_after = float(frame.get("after_s", 5))
                        continue
                    await self._broker.publish(channel, frame)
        except TimeoutError:
            logger.warning(
                "turn %s timed out (>%ss) for topic %s; interrupted",
                turn_id, self._timeout, topic_id,
            )
            await self._broker.publish(
                channel,
                {
                    "type": "error",
                    "message": "芝士这轮超时被中断了（可能卡在某步）。"
                    "已完成的改动已保存，马上自动接着跑一次。",
                },
            )
            if not is_resume:
                resume_after = 10.0
        except AppError as exc:
            await self._broker.publish(
                channel, {"type": "error", "message": exc.message}
            )
        except Exception:  # noqa: BLE001 — surface agent/runtime failures (spec H4)
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
            self._schedule_resume(chat_service, topic_id, resume_after)
