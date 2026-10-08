"""One subscriber's queue of frames, and how far behind it is allowed to fall.

The broker in `runtime` publishes into these; the websocket route drains them.
They live apart from the broker because `runtime.py` is past its size cap
(`.claude/rules/architecture.md`), and what is here is what that cap made room
for: the queue, the budget in bytes it is held to, and `cut_off`, the policy
for one that ran past it.
"""

import asyncio
import json
import logging
from collections import deque
from collections.abc import Iterable

logger = logging.getLogger("cheesex.runtime")

# Channel = the topic id (str). Frames are the same dicts converse yields.
Frame = dict

# How far behind one connection may fall, in bytes of published frames. Without
# a bound, a subscriber that reads slower than a turn publishes grows its queue
# forever — those frames are held by the process, not by the socket, and
# uvicorn's ping/pong cannot see it: a consumer answering pings at a trickle
# stays perfectly alive while the backlog climbs. Past the budget the
# connection is cut off and told to come back, the same answer
# `preview_hub.MAX_QUEUED_BYTES` gives a preview stream. That costs the client
# a reconnect — it refetches history and the broker replays the turn — instead
# of costing the process its memory.
MAX_SUBSCRIBER_BYTES = 1024 * 1024


class SubscriberOverflow:
    """The last item a cut-off subscriber queue holds.

    A sentinel rather than a frame: the route must close the socket, not
    forward it. It reaches the consumer through the queue because the
    publisher is a background turn with no socket to close — the relay is the
    one that can hang up. Only `SubscriberQueue` ever queues it.
    """


OVERFLOW = SubscriberOverflow()


def _frame_bytes(frame: Frame) -> int:
    """What one frame costs the consumer that holds it: its serialised size.

    A frame count cannot stand in for it — one `live` frame carries a whole
    draft, one tool frame a whole result. This is the same JSON `send_json`
    builds per connection, so it adds no work the route would not do anyway.
    `default=str` keeps a frame no socket could carry failing at the socket,
    where it fails today, instead of taking its publisher down over a length.
    """
    return len(
        json.dumps(
            frame, ensure_ascii=False, separators=(",", ":"), default=str
        ).encode()
    )


class SubscriberQueue(asyncio.Queue[Frame | SubscriberOverflow]):
    """One connection's queue, with a byte budget instead of no bound at all.

    Publishing must never block — one slow consumer may not stall the turn
    publishing to everyone else — so the budget is enforced on the way in: past
    it this queue takes nothing more and is dropped from its channel. It stays
    an `asyncio.Queue` (`get`, `get_nowait`, `qsize`), because that is how the
    route and the tests read a subscriber.
    """

    def __init__(self, max_bytes: int = MAX_SUBSCRIBER_BYTES) -> None:
        super().__init__()
        self.max_bytes = max_bytes
        self.queued_bytes = 0
        self.overflowed = False
        # The sizes of the frames still queued, oldest first, so `_get` can
        # give the budget back. asyncio.Queue's own `_get` is the documented
        # seam for a subclass that must see every item.
        self._sizes: deque[int] = deque()

    def offer(self, frame: Frame) -> bool:
        """Queue one frame if the backlog can still afford it.

        One frame always fits an empty queue: the budget bounds a backlog, and
        refusing a frame that is already in memory would cut off a connection
        that is keeping up because the room happened to publish one large
        block. Returns False once this queue is out of the running.
        """
        if self.overflowed:
            return False
        size = _frame_bytes(frame)
        if not self.empty() and self.queued_bytes + size > self.max_bytes:
            self.overflowed = True
            self.put_nowait(OVERFLOW)
            return False
        self._take(frame, size)
        return True

    def prefill(self, frames: Iterable[Frame]) -> int:
        """Seed a new subscriber with the replay buffer's catch-up frames.

        Trimmed from the OLDEST end when they do not all fit — the newest
        frames are the ones a mid-turn reconnect is missing — and never a
        reason to cut the connection off: the buffer is already bounded, and a
        client refused on every connect would reconnect forever. Returns how
        many frames it had to drop.
        """
        sized = [(frame, _frame_bytes(frame)) for frame in frames]
        used = 0
        kept: list[tuple[Frame, int]] = []
        for frame, size in reversed(sized):
            if kept and used + size > self.max_bytes:
                break
            used += size
            kept.append((frame, size))
        for frame, size in reversed(kept):
            self._take(frame, size)
        return len(sized) - len(kept)

    def _take(self, frame: Frame, size: int) -> None:
        self.queued_bytes += size
        self._sizes.append(size)
        self.put_nowait(frame)

    def _get(self) -> Frame | SubscriberOverflow:
        item = super()._get()
        # The sentinel is a terminator, not a frame: it never carried bytes,
        # and it is always the last item in the queue.
        if not isinstance(item, SubscriberOverflow):
            self.queued_bytes -= self._sizes.popleft()
        return item


def cut_off(
    subs_by_channel: dict[str, set[SubscriberQueue]],
    channel: str,
    queue: SubscriberQueue,
) -> None:
    """Stop feeding a connection that ran past its budget, and say so.

    Dropped from the channel here rather than when its route notices: the
    relay can be parked in a socket write for as long as the client is slow,
    and every frame published meanwhile is exactly the memory this bound
    exists for. Its queue is left holding the `OVERFLOW` sentinel for the
    relay to close on. The logger keeps the runner's name so the line still
    lands where the broker's others do.
    """
    subs = subs_by_channel.get(channel)
    if subs is not None:
        subs.discard(queue)
        if not subs:
            subs_by_channel.pop(channel, None)
    logger.warning(
        "broker_subscriber_overflow channel=%s queued_bytes=%d max_bytes=%d",
        channel,
        queue.queued_bytes,
        queue.max_bytes,
    )
