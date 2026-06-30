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
    """Fan-out pub/sub for one process. Each subscriber gets every frame
    published to its channel from the moment it subscribes (no backlog yet —
    reconnect-mid-turn replay is the R3 upgrade)."""

    def __init__(self) -> None:
        self._subs: dict[str, set[asyncio.Queue[Frame]]] = {}

    async def publish(self, channel: str, frame: Frame) -> None:
        for q in list(self._subs.get(channel, ())):
            q.put_nowait(frame)

    @contextlib.asynccontextmanager
    async def subscribe(self, channel: str) -> AsyncIterator[asyncio.Queue[Frame]]:
        q: asyncio.Queue[Frame] = asyncio.Queue()
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

    def __init__(self, broker: InProcessBroker) -> None:
        self._broker = broker
        # Keep references so tasks aren't GC'd mid-flight (and for shutdown).
        self._tasks: set[asyncio.Task] = set()

    def submit(
        self,
        chat_service,
        topic_id: uuid.UUID,
        *,
        author: str,
        content: str,
        summon: bool,
    ) -> uuid.UUID:
        """Start a turn in the background; return its turn_id immediately. Turns
        on the same topic serialize on ChatService's per-topic lock (so a second
        submit queues behind the first)."""
        turn_id = uuid.uuid4()
        task = asyncio.create_task(
            self._run(
                chat_service, topic_id, turn_id,
                author=author, content=content, summon=summon,
            )
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return turn_id

    async def _run(
        self,
        chat_service,
        topic_id: uuid.UUID,
        turn_id: uuid.UUID,
        *,
        author: str,
        content: str,
        summon: bool,
    ) -> None:
        channel = str(topic_id)
        try:
            async for frame in chat_service.converse(
                topic_id=topic_id, author=author, content=content, summon=summon
            ):
                await self._broker.publish(channel, frame)
        except AppError as exc:
            await self._broker.publish(
                channel, {"type": "error", "message": exc.message}
            )
        except Exception:  # noqa: BLE001 — surface agent/runtime failures (spec H4)
            logger.exception("turn %s failed for topic %s", turn_id, topic_id)
            await self._broker.publish(
                channel,
                {
                    "type": "error",
                    "message": "芝士这轮中断了（偶发的沙箱/模型错误）。"
                    "它已完成的改动已保存，再 @ 它一次就会接着来。",
                },
            )
