"""Process-local fan-out, replay and member activity for realtime subscribers."""

import contextlib
import logging
import time
from collections import OrderedDict
from collections.abc import AsyncIterator, Callable, Iterable
from functools import lru_cache

from app.domain.agent.realtime.activity import LIVE_ONLY, RoomActivity
from app.domain.agent.realtime.subscriber_queue import (
    MAX_SUBSCRIBER_BYTES,
    Frame,
    SubscriberQueue,
    cut_off,
)

logger = logging.getLogger("cheesex.runtime")

#: How many turn ends `adopt` remembers. Only a database read still in flight
#: when a turn ended needs its end remembered, and such a read takes
#: milliseconds; this many ends cannot happen inside one.
ENDS_KEPT = 1024


class InProcessBroker:
    """Fan-out pub/sub for one process, with a per-channel replay buffer of the
    IN-PROGRESS turn's ephemeral frames (R3). A connection that subscribes mid-turn
    gets those frames immediately (catch-up), then the live continuation — so a
    reconnect (after `GET /blocks` for persisted history) is seamless.

    Active work is keyed by its compatibility id. A message folded into a live
    Claude session emits no synthetic completion boundary; only the session's
    existing lifecycle markers own active state.
    """

    def __init__(
        self,
        replay_size: int = 512,
        max_subscriber_bytes: int = MAX_SUBSCRIBER_BYTES,
    ) -> None:
        self._subs: dict[str, set[SubscriberQueue]] = {}
        self._buffer: dict[str, list[Frame]] = {}
        self._active: dict[str, set[str]] = {}
        self._active_since: dict[tuple[str, str], float] = {}
        # Who is typing or working here; reads the turn starts above.
        self.activity = RoomActivity(self._active_since)
        self._last_activity_at: dict[str, float] = {}
        # Every turn end this process relayed, numbered in order: what `adopt`
        # checks a database read against (`books_read`).
        self._ends = 0
        self._ended: OrderedDict[tuple[str, str], int] = OrderedDict()
        self._replay_size = replay_size
        self._max_subscriber_bytes = max_subscriber_bytes
        #: Called with a channel when it starts having a turn in flight or stops:
        #: what a project's channel list shows as 芝士 at work there. Kept across
        #: `reset`, since whoever listens registered once.
        self.busy_changed: list[Callable[[str], None]] = []

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
        self.activity.reset()
        self._last_activity_at.clear()
        self._ended.clear()

    def books_read(self) -> int:
        """Take before reading a channel's open turns from the database, and
        hand to `adopt` with what the read returned."""
        return self._ends

    def adopt(
        self,
        channel: str,
        turns: Iterable[tuple[str, float, str | None]],
        *,
        read_at: int,
    ) -> list[tuple[str, str | None]]:
        """Take in live turns without synthesising or buffering turn progress.

        ``turns`` were read from the database after ``read_at``
        (`books_read`). A turn whose end this process relayed since then is
        left out: the read can have run before the end was written, and
        nothing would end the turn a second time, so its agent would show
        working until the next reconciliation.

        Existing turns keep their original start and attribution. Adopted turns
        end on the same ``turn_finished`` boundary as locally observed turns.
        Return the newly adopted ids and seats so callers can announce them.
        """
        taken: list[tuple[str, str | None]] = []
        for turn_id, started, agent in turns:
            if turn_id in self._active.get(channel, ()):
                continue
            if self._ended.get((channel, turn_id), -1) > read_at:
                continue
            went_busy = not self._active.get(channel)
            self._active.setdefault(channel, set()).add(turn_id)
            if went_busy:
                self._busy_changed(channel)
            self._active_since.setdefault((channel, turn_id), started)
            taken.append((turn_id, agent))
            if agent:
                for started_frame in self.activity.turn_started(
                    channel, turn_id, agent
                ):
                    self._fan_out(channel, started_frame)
        return taken

    async def publish(self, channel: str, frame: Frame) -> None:
        kind = frame.get("type")
        # Standalone facts, not turn progress: fanned out live, never buffered. A
        # reconnect reads each back whole (reactions from GET /blocks, session state
        # from GET /topics/{id}/agent/control, member activity from the snapshot on
        # connect, a comment thread's progress from its thread list); buffering would
        # replay states that have moved on and make an idle channel look in_flight.
        if kind in LIVE_ONLY:
            self._fan_out(channel, frame)
            return
        # A person's message landing ends their typing in this room.
        author = (frame.get("block") or {}).get("author")
        if kind == "user_block" and author:
            await self.typing(channel, str(author), active=False)
        followed: list[Frame] = []
        if kind == "turn_started":
            turn_id = str(frame.get("turn_id") or "")
            if turn_id:
                went_busy = not self._active.get(channel)
                self._active.setdefault(channel, set()).add(turn_id)
                if went_busy:
                    self._busy_changed(channel)
                self._active_since.setdefault((channel, turn_id), time.time())
                if agent := frame.get("agent"):
                    followed += self.activity.turn_started(channel, turn_id, str(agent))

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
            self._ends += 1
            self._ended[(channel, turn_id)] = self._ends
            self._ended.move_to_end((channel, turn_id))
            if len(self._ended) > ENDS_KEPT:
                self._ended.popitem(last=False)
            active = self._active.get(channel)
            if active is not None:
                active.discard(turn_id)
                if (ended := self.activity.turn_finished(channel, turn_id)) is not None:
                    followed.append(ended)
                self._active_since.pop((channel, turn_id), None)
                if not active:
                    self._active.pop(channel, None)
                    self._buffer.pop(channel, None)
                    self._last_activity_at.pop(channel, None)
                    self._busy_changed(channel)

        self._fan_out(channel, frame)
        for change in followed:
            for to, extra in self.activity.told(channel, change):
                self._fan_out(to, extra)

    def _busy_changed(self, channel: str) -> None:
        for listener in self.busy_changed:
            try:
                listener(channel)
            except Exception:
                logger.exception("broker_busy_listener_failed channel=%s", channel)

    def has_subscribers(self, channel: str) -> bool:
        """Whether anyone is listening on this channel in this process."""
        return bool(self._subs.get(channel))

    def any_subscribed(self, prefix: str) -> bool:
        """Whether anyone is listening on a channel whose name starts so."""
        return any(channel.startswith(prefix) for channel in self._subs)

    def _fan_out(self, channel: str, frame: Frame) -> None:
        if subs := self._subs.get(channel):
            for q in list(subs):
                if not q.offer(frame):
                    cut_off(self._subs, channel, q)

    async def typing(self, channel: str, member: str, *, active: bool) -> None:
        """A person composing in this room's input (or stopping)."""
        if (frame := self.activity.typing(channel, member, active)) is not None:
            self._fan_out(channel, frame)

    def in_flight(self, channel: str) -> bool:
        """True while at least one explicitly-started turn is active. Lets a
        (re)connecting client rebuild the 正在思考 indicator instead of showing
        a silent, seemingly-dead topic."""
        return bool(self._active.get(channel))

    def active_turn_ids(self, channel: str) -> list[str]:
        """Stable snapshot for a reconnecting client."""
        return sorted(self._active.get(channel, ()))

    def active_turns_since(self, channel: str) -> dict[str, float]:
        """When each live turn on this channel started, in epoch seconds."""
        return {
            turn_id: self._active_since[(channel, turn_id)]
            for turn_id in self.active_turn_ids(channel)
            if (channel, turn_id) in self._active_since
        }

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
    ) -> AsyncIterator[SubscriberQueue]:
        q = SubscriberQueue(self._max_subscriber_bytes)
        if replay:
            # Catch-up goes through the same budget, trimmed rather than
            # refused: a fresh connection must not be cut off by a buffer it
            # did not cause, and the frames are history the client can also
            # re-read.
            if dropped := q.prefill(self._buffer.get(channel, ())):
                logger.warning(
                    "broker_replay_trimmed channel=%s dropped=%d",
                    channel,
                    dropped,
                )
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
