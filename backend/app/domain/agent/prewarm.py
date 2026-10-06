"""Bringing a quiet seat's session up to date before anyone writes to it.

A session reads what it was started with once and keeps it, and it is compared
with what the backend would start today only when it is started
(`screen_identity`). So a release that changes how sessions are launched left
every running one as it was until its next message, and that message waited for
the new session to come up — a cold resume with its tools reconnecting, about
20 s on dev. The comparison is made here instead, as soon as the seat is quiet:
after the process that took the room over has read it up, and whenever a
session whose relaunch was put off stops working.

A session whose runner let it go after sitting idle is started again the same
way when somebody opens its room or starts typing there (`room_active`): a
message is likely on its way, and the session can be up before it arrives.

The start goes through the same `RoomSessions.ensure` a turn uses, from the
same inputs (`RoomTurns._launch_inputs`) and under the same seat lock, so a turn
arriving meanwhile waits for it rather than racing it, and the session it
leaves is the one that turn would have kept. What a session is doing is never
cut short: the channel puts a relaunch off while the session works or has tasks
running, and the seat comes back here when it goes quiet. Anything that fails
leaves the seat to the next turn, which starts it as it always did.
"""

import asyncio
import logging
import time
import uuid
from typing import TYPE_CHECKING, Protocol

from app.domain.agent.admission import HostMemory
from app.domain.agent.harness import SessionRef
from app.domain.agent.seat_admission import seat_admission

if TYPE_CHECKING:
    from app.domain.agent.compute import ComputePool
    from app.domain.agent.room.turn import _Launch

logger = logging.getLogger(__name__)

#: How many seats are brought up to date at once. One: after a release a dozen
#: seats are due together, and restarting them all at the same moment is a
#: burst of new sessions on the one host they share.
AT_ONCE = 1
#: A room opened or typed in again within this long is not looked at again:
#: typing is reported on every burst of keys, and one look per minute is
#: enough to have the session up before the message is sent.
ROOM_AGAIN_AFTER_S = 60.0


class _Rooms(Protocol):
    """What bringing a seat up to date asks of the service (`ChatService`):
    where its session runs, the lock its turns take, and what a turn would
    start it with."""

    _compute: "ComputePool"

    def _seat_lock_for(
        self, topic_id: uuid.UUID, agent_handle: str
    ) -> asyncio.Lock: ...

    async def _launch_inputs(
        self, topic_id: uuid.UUID, agent_handle: str, *, acting: str
    ) -> "_Launch | None": ...

    async def _turn_seat_handle(self, topic_id: uuid.UUID) -> str: ...


class SeatPrewarm:
    def __init__(self, chat: _Rooms) -> None:
        self._chat = chat
        self._slots = asyncio.Semaphore(AT_ONCE)
        self._running: dict[tuple[uuid.UUID, str], asyncio.Task] = {}
        self._memory = HostMemory()
        self._room_seen: dict[uuid.UUID, float] = {}
        self._looking: set[asyncio.Task] = set()

    def room_active(self, topic_id: uuid.UUID) -> None:
        """Somebody opened this room or is typing in it: have the session the
        next message goes to up before it is sent, at most once a minute."""
        now = time.monotonic()
        if now - self._room_seen.get(topic_id, float("-inf")) < ROOM_AGAIN_AFTER_S:
            return
        self._room_seen = {
            room: seen
            for room, seen in self._room_seen.items()
            if now - seen < ROOM_AGAIN_AFTER_S
        }
        self._room_seen[topic_id] = now
        task = asyncio.create_task(self._room_active(topic_id))
        self._looking.add(task)
        task.add_done_callback(self._looking.discard)

    async def _room_active(self, topic_id: uuid.UUID) -> None:
        try:
            seat = await self._chat._turn_seat_handle(topic_id)
        except Exception:  # noqa: BLE001 — a room it cannot read is the turn's to report
            logger.warning("session_prewarm_seat_unknown topic=%s", topic_id)
            return
        runtime = self._chat._compute.seat_runtime(topic_id, seat)
        live = runtime.live.get((topic_id, seat)) if runtime is not None else None
        if live is not None:
            self.nudge(live.session)

    def nudge(self, session: SessionRef) -> None:
        """Bring this seat's session up to date in the background, once."""
        key = (session.topic_id, session.agent_handle)
        running = self._running.get(key)
        if running is not None and not running.done():
            return
        task = asyncio.create_task(
            self.prewarm(session),
            name=f"prewarm:{session.topic_id}/{session.agent_handle}",
        )
        self._running[key] = task

        def forget(done: asyncio.Task, key=key) -> None:
            if self._running.get(key) is done:
                del self._running[key]

        task.add_done_callback(forget)

    async def prewarm(self, session: SessionRef) -> str:
        """Restart the seat's session if it is quiet and no longer what a turn
        would start; what happened, for the log."""
        started = time.monotonic()
        try:
            outcome = await self._prewarm(session)
        except Exception:  # noqa: BLE001 — the next turn starts it the usual way
            logger.exception(
                "session_prewarm_failed topic=%s seat=%s",
                session.topic_id,
                session.agent_handle,
            )
            return "failed"
        logger.info(
            "session_prewarm topic=%s seat=%s outcome=%s elapsed_ms=%.0f",
            session.topic_id,
            session.agent_handle,
            outcome,
            (time.monotonic() - started) * 1000,
        )
        return outcome

    async def _prewarm(self, session: SessionRef) -> str:
        chat = self._chat
        topic_id, seat = session.topic_id, session.agent_handle
        runtime = chat._compute.seat_runtime(topic_id, seat)
        if runtime is None:
            return "not_held"
        async with self._slots, seat_admission(chat._seat_lock_for(topic_id, seat)):
            live = runtime.prewarm_due(session)
            if live is None:
                return "not_due"
            if not await self._memory.has_room(topic_id):
                return "host_full"
            launch = await chat._launch_inputs(topic_id, seat, acting=live.acting)
            if launch is None or launch.runtime is not runtime:
                # Archived, gone, or now on another harness: that move is the
                # next turn's to make (`ComputePool.activate`).
                return "not_here"
            await runtime.ensure(
                launch.session,
                system_prompt=launch.system_prompt,
                resume_token=launch.resume_token,
                model=launch.model,
                env=launch.env,
                acting=live.acting,
                needs_place=launch.needs_place,
            )
            return "deferred" if runtime.prewarm_due(session) else "current"
