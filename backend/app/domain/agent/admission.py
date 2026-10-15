"""What a turn waits for before it starts, and what the room is told meanwhile.

Two things can be full. The project's slots (``max_concurrent_turns``) are the
project's share of the platform. The session host is what every project's
sessions share: one machine, one kernel. A session started there when it has no
memory left does not fail alone — the machine stalls in swap and takes every
room on it, and whatever else runs beside them, down together (#1544). So a turn
that would start a new session on the host waits until the host has room for
one more session at its cap, and the room reads the same queue notice it reads
when the project is full.

Beside those two this module holds what a seat and a room are serialized by
(``Lease``): one at a time inside a process, and now one at a time across the
processes, which an ``asyncio.Lock`` alone could not say.
"""

import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.config import settings
from app.core.redis import get_redis_client
from app.core.sentences import say
from app.domain.notice_meta import (
    EVENT_TURN_QUEUED,
    SEVERITY_INFO,
    WHO_PLATFORM,
    notice,
)

logger = logging.getLogger(__name__)

#: The variable the launcher reads to start a session under its memory cap.
SESSION_MEMORY_ENV = "CHEESE_SESSION_MEMORY_MAX"
#: How long one reading of the host's memory answers for. Turns admitted in a
#: burst share it rather than each asking the machine.
READING_TTL_S = 2.0
#: How often a turn held for the host's memory looks again.
RECHECK_S = 5.0

#: Where one reading of a session host's free memory is kept for the whole box.
_SHARED_KEY = "session-host-memory:{host}"
#: The value standing for "the host could not be read": cached like a number,
#: because an unread host holds nobody and must not be re-probed any faster.
_UNREAD = b""


class _NoReading:
    """Why `_shared_reading` has no number to answer from."""

    def __init__(self, local_cache: bool) -> None:
        #: Whether this process's own reading may stand in. It may when there is
        #: no shared reading to keep in step with; it may not when the shared one
        #: merely expired, or the box would stop re-probing.
        self.local_cache = local_cache


#: Valkey answered, nobody has published one inside the TTL: read the host.
_EMPTY = _NoReading(local_cache=False)
#: No Valkey, or it did not answer: this process's own reading is all there is.
_UNUSABLE = _NoReading(local_cache=True)


async def _shared_reading(host: str) -> int | None | _NoReading:
    """What this box last found free on ``host``, or why there is nothing to
    answer from. Every backend process reads the same one, so they all hold the
    same turns back — the gate is for the host, not for a process."""
    redis = get_redis_client()
    if redis is None:
        return _UNUSABLE
    try:
        raw = await redis.get(_SHARED_KEY.format(host=host))
    except Exception:  # noqa: BLE001 — Valkey down: the local reading stands
        logger.warning("shared host memory reading unavailable", exc_info=True)
        return _UNUSABLE
    if raw is None:
        return _EMPTY
    if raw == _UNREAD:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return _EMPTY


async def _publish_reading(host: str, value: int | None) -> None:
    """Put one reading where every process will answer from it, for one TTL.
    Failing to is not an error: the next read simply asks the host again."""
    redis = get_redis_client()
    if redis is None:
        return
    try:
        await redis.set(
            _SHARED_KEY.format(host=host),
            _UNREAD if value is None else str(value).encode(),
            px=int(READING_TTL_S * 1000),
        )
    except Exception:  # noqa: BLE001
        logger.warning("shared host memory reading not published", exc_info=True)


#: 排队不是故障：平台自己会往前推，没人需要动手。
QUEUED_META = notice(
    EVENT_TURN_QUEUED,
    severity=SEVERITY_INFO,
    who=WHO_PLATFORM,
    detail=say("turnQueuedDetail"),
    detail_label=say("labelWhatHappensNext"),
)
#: 整机内存不够时排队：前面不一定有别的轮次，等的是资源。
HOST_BUSY_META = notice(
    EVENT_TURN_QUEUED,
    severity=SEVERITY_INFO,
    who=WHO_PLATFORM,
    detail=say("turnQueuedHostBusyDetail"),
    detail_label=say("labelWhatHappensNext"),
)


def queued_text(ahead: int) -> str:
    """Platform copy for a turn the project's slots hold back."""
    if ahead <= 0:
        return say("turnQueued")
    return say("turnQueuedBehind", ahead=ahead)


def session_memory_max(device_id: str) -> str | None:
    """The cap a session started on ``device_id`` runs under, as systemd spells
    it; None on any machine but the session host, which is the only one whose
    kernel every room shares."""
    mb = settings.agent_session_memory_max_mb
    if mb <= 0 or device_id != settings.agent_session_device_id:
        return None
    return f"{mb}M"


def available_bytes(meminfo: str) -> int | None:
    """``MemAvailable`` out of ``/proc/meminfo``: what the kernel can hand a new
    process without swapping, page cache it can drop included."""
    for line in meminfo.splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) * 1024
    return None


class HostMemory:
    """Whether the session host has room for the session a turn would start.

    A room whose session is already running on the host starts nothing, so it
    is never held: holding it would stop the rooms that are using the memory
    from finishing with it. A host that cannot be read (not Linux, unreachable)
    holds nobody either — the gate exists to keep the host up, and a turn that
    cannot reach the host fails on its own with the reason.

    The reading is kept once for the whole box (`_shared_reading`), not once
    per process: the gate is for the host, so N backends must not each decide
    from a reading of their own. Without Valkey each process keeps its own, as
    before.
    """

    def __init__(self, hub=None):
        self._hub = hub
        self._lock = asyncio.Lock()
        self._read_at = float("-inf")
        self._available: int | None = None

    async def has_room(self, topic_id: uuid.UUID) -> bool:
        host = settings.agent_session_device_id
        mb = settings.agent_session_memory_max_mb
        if not host or mb <= 0:
            return True
        hub = self._hub
        if hub is None:
            # deferred-import: tests replace this name on app.domain.agent.device_hub
            from app.domain.agent.device_hub import device_hub as hub
        if any(screen.device_id == host for screen in hub.screens_for_topic(topic_id)):
            return True
        available = await self._read(hub, host)
        return available is None or available >= mb * 1024 * 1024

    async def can_start(self, mb: int) -> bool:
        """Whether the session host has ``mb`` free for a session that is not
        running yet (a person's 芝士). Unreadable holds nobody, as above."""
        host = settings.agent_session_device_id
        if not host:
            return True
        hub = self._hub
        if hub is None:
            # deferred-import: tests replace this name on app.domain.agent.device_hub
            from app.domain.agent.device_hub import device_hub as hub
        available = await self._read(hub, host)
        return available is None or available >= mb * 1024 * 1024

    async def _read(self, hub, host: str) -> int | None:
        shared = await _shared_reading(host)
        if not isinstance(shared, _NoReading):
            return shared
        async with self._lock:
            # One probe at a time within this process, and one answer to hand
            # back either way.
            shared = await _shared_reading(host)
            if not isinstance(shared, _NoReading):
                return shared
            if shared.local_cache and (
                time.monotonic() - self._read_at < READING_TTL_S
            ):
                return self._available
            value = await self._probe(hub, host)
            self._available, self._read_at = value, time.monotonic()
            await _publish_reading(host, value)
            return value

    async def _probe(self, hub, host: str) -> int | None:
        """What the host says it has free right now; None when it will not say."""
        try:
            result = await hub.exec(host, ["cat", "/proc/meminfo"], timeout=5)
            return (
                available_bytes(result.get("stdout") or "")
                if result.get("exit") == 0
                else None
            )
        except Exception:  # noqa: BLE001 — an unread host holds nobody
            logger.warning("session host memory unreadable", exc_info=True)
            return None


async def wait_for_host(
    has_room: Callable[[uuid.UUID], Awaitable[bool]] | None,
    topic_id: uuid.UUID,
    tell_room: Callable[[str], Awaitable[object]],
) -> None:
    """Hold a turn until the host has room, telling its room once that it waits."""
    told = False
    while has_room is not None and not await has_room(topic_id):
        if not told:
            told = True
            await tell_room(say("turnQueuedHostBusy"))
        await asyncio.sleep(RECHECK_S)


# --- whose turn: one at a time, and at most so many at once ------------------
#
# Every agent kind waits the same way before it runs: a conversation answers one
# question at a time (a ``Hold``), and a project runs at most so many answers at
# once (a ``Pool``). Both live in Valkey, so they hold across backend processes
# and outlive none of them: a slot is leased, renewed while its holder works, and
# lapses ``LEASE_S`` after a backend that died stopped renewing it. The pool is
# first come, first served, which is what lets a waiter be told how many are
# ahead of it.

#: How long a slot, a hold or a place in a queue lasts unless renewed: the most
#: a backend that died keeps it for.
LEASE_S = 30.0
#: How often a held slot and hold are renewed.
RENEW_S = 10.0
#: How often a waiter looks again.
POLL_S = 1.0
#: How long an idle pool's keys are kept.
POOL_TTL_MS = 24 * 3600 * 1000

# KEYS: holders, queue, alive, seq. ARGV: ticket, lease ms, limit, keys ttl ms.
# -1 = admitted (or already holding); otherwise how many are queued ahead.
_ENTER = """
local holders, queue, alive, seq = KEYS[1], KEYS[2], KEYS[3], KEYS[4]
local ticket, lease, limit = ARGV[1], tonumber(ARGV[2]), tonumber(ARGV[3])
local t = redis.call('TIME')
local now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
for _, key in ipairs(KEYS) do redis.call('PEXPIRE', key, ARGV[4]) end
redis.call('ZREMRANGEBYSCORE', holders, '-inf', now)
for _, gone in ipairs(redis.call('ZRANGEBYSCORE', alive, '-inf', now)) do
  redis.call('ZREM', queue, gone)
  redis.call('ZREM', alive, gone)
end
if redis.call('ZSCORE', holders, ticket) then
  redis.call('ZADD', holders, now + lease, ticket)
  return -1
end
if not redis.call('ZSCORE', queue, ticket) then
  redis.call('ZADD', queue, redis.call('INCR', seq), ticket)
end
redis.call('ZADD', alive, now + lease, ticket)
local rank = redis.call('ZRANK', queue, ticket)
if rank < limit - redis.call('ZCARD', holders) then
  redis.call('ZREM', queue, ticket)
  redis.call('ZREM', alive, ticket)
  redis.call('ZADD', holders, now + lease, ticket)
  return -1
end
return rank
"""
# KEYS: holders. ARGV: ticket, lease ms. 1 = renewed, 0 = no longer held.
_RENEW_SLOT = """
local t = redis.call('TIME')
local now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
if redis.call('ZSCORE', KEYS[1], ARGV[1]) then
  redis.call('ZADD', KEYS[1], now + tonumber(ARGV[2]), ARGV[1])
  return 1
end
return 0
"""
# KEYS: hold. ARGV: value, lease ms. Only the holder's own value is touched.
_RENEW_HOLD = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('PEXPIRE', KEYS[1], ARGV[2])
end
return 0
"""
# KEYS: holders. ARGV: ticket, lease ms. A place a turn already had, taken back
# whether or not it lapsed meanwhile.
_TAKE_BACK = """
local t = redis.call('TIME')
local now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
redis.call('ZADD', KEYS[1], now + tonumber(ARGV[2]), ARGV[1])
return 1
"""
_DROP_HOLD = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('DEL', KEYS[1])
end
return 0
"""


@dataclass(frozen=True)
class Pool:
    """At most ``limit`` at once under ``key``, first come first served."""

    key: str
    limit: int

    def keys(self) -> list[str]:
        return [f"admission:{self.key}:{part}" for part in _POOL_PARTS]


_POOL_PARTS = ("holders", "queue", "alive", "seq")


@dataclass(frozen=True)
class Hold:
    """One at a time under ``key``. ``value`` is what a reader of the hold
    finds there (who is asking, for the document's 芝士); the ticket when
    left empty."""

    key: str
    value: str = ""


def _ms(seconds: float) -> int:
    return int(seconds * 1000)


class Slot:
    """A turn let in: renewed until released. Releasing twice is harmless."""

    def __init__(
        self,
        redis: Redis | None,
        ticket: str,
        pool: Pool | None,
        hold: tuple[str, str] | None,
    ) -> None:
        self._redis = redis
        self._ticket = ticket
        self._pool = pool
        self._hold = hold
        self._renewing = (
            asyncio.create_task(self._renew(), name=f"admission-renew-{ticket}")
            if redis is not None and (pool is not None or hold is not None)
            else None
        )

    async def _renew(self) -> None:
        assert self._redis is not None
        while True:
            await asyncio.sleep(RENEW_S)
            try:
                if self._pool is not None:
                    renewed = await self._redis.eval(  # type: ignore[misc]
                        _RENEW_SLOT,
                        1,
                        self._pool.keys()[0],
                        self._ticket,
                        _ms(LEASE_S),
                    )
                    if not renewed:
                        logger.warning(
                            "slot %s in %s lapsed before it was released",
                            self._ticket,
                            self._pool.key,
                        )
                if self._hold is not None:
                    await self._redis.eval(  # type: ignore[misc]
                        _RENEW_HOLD, 1, self._hold[0], self._hold[1], _ms(LEASE_S)
                    )
            except Exception:  # noqa: BLE001 — the next renewal tries again
                logger.warning("renewing slot %s failed", self._ticket, exc_info=True)

    async def release(self) -> None:
        if self._renewing is not None:
            self._renewing.cancel()
            self._renewing = None
        redis, self._redis = self._redis, None
        if redis is None:
            return
        try:
            if self._pool is not None:
                holders, queue, alive, _seq = self._pool.keys()
                for key in (holders, queue, alive):
                    await redis.zrem(key, self._ticket)
            if self._hold is not None:
                await redis.eval(_DROP_HOLD, 1, *self._hold)  # type: ignore[misc]
        except Exception:  # noqa: BLE001 — what is not released lapses by itself
            logger.warning("releasing slot %s failed", self._ticket, exc_info=True)

    def leave(self) -> None:
        """Stop renewing the slot without letting it go: another process is
        taking the turn over and holds it from here (``adopt``)."""
        if self._renewing is not None:
            self._renewing.cancel()
            self._renewing = None
        self._redis = None

    def held(self) -> "Held":
        """The slot as it is written down, for a process that takes the turn
        over (``adopt``)."""
        return Held(
            self._ticket,
            self._pool.key if self._pool is not None else None,
            self._hold[0] if self._hold is not None else None,
            self._hold[1] if self._hold is not None else None,
        )


@dataclass(frozen=True)
class Held:
    """A slot as another process finds it: its ticket, its place in a pool and
    its hold, by key."""

    ticket: str
    pool: str | None = None
    hold_key: str | None = None
    hold_value: str | None = None


async def adopt(redis: Redis, held: Held) -> Slot | None:
    """Go on holding a slot another process took for a turn that is still
    running; None when its hold went to another turn meanwhile. A place in the
    pool that lapsed is taken back: the turn was running all along."""
    hold = None
    if held.hold_key is not None and held.hold_value is not None:
        hold = (held.hold_key, held.hold_value)
        kept = await redis.eval(_RENEW_HOLD, 1, *hold, _ms(LEASE_S))  # type: ignore[misc]
        if not kept and not await redis.set(*hold, nx=True, px=_ms(LEASE_S)):
            return None
    pool = None
    if held.pool is not None:
        pool = Pool(held.pool, 0)
        await redis.eval(_TAKE_BACK, 1, pool.keys()[0], held.ticket, _ms(LEASE_S))  # type: ignore[misc]
    return Slot(redis, held.ticket, pool, hold)


async def enter(
    redis: Redis | None,
    ticket: str,
    *,
    pool: Pool | None = None,
    hold: Hold | None = None,
    wait_s: float | None = None,
    on_queued: Callable[[int], Awaitable[object]] | None = None,
    give_up: Callable[[], Awaitable[bool]] | None = None,
) -> Slot | None:
    """Wait for ``hold`` and then a place in ``pool``; the slot, or None when
    neither came within ``wait_s`` (``0`` = do not wait, ``None`` = for as long
    as it takes) or ``give_up`` said to stop. ``on_queued`` is told once, the
    first time the turn has to wait, how many are ahead of it.

    ``ticket`` names the turn: entering again under the same ticket finds the
    slot it already holds, which is how a turn taken up again after a restart
    keeps its place instead of taking a second one. Without Valkey there is
    nothing to wait for and the turn is let in."""
    if redis is None:
        return Slot(None, ticket, None, None)
    deadline = None if wait_s is None else time.monotonic() + wait_s
    held = None if hold is None else (hold.key, hold.value or ticket)
    have_hold = held is None
    told = False

    async def abandon() -> None:
        await Slot(redis, ticket, pool, held if have_hold else None).release()

    try:
        while True:
            ahead = 0
            if not have_hold:
                assert held is not None
                have_hold = bool(
                    await redis.set(held[0], held[1], nx=True, px=_ms(LEASE_S))
                )
            if have_hold:
                if held is not None:
                    await redis.eval(_RENEW_HOLD, 1, *held, _ms(LEASE_S))  # type: ignore[misc]
                if pool is None:
                    return Slot(redis, ticket, None, held)
                ahead = int(
                    await redis.eval(  # type: ignore[misc]
                        _ENTER,
                        4,
                        *pool.keys(),
                        ticket,
                        _ms(LEASE_S),
                        pool.limit,
                        POOL_TTL_MS,
                    )
                )
                if ahead < 0:
                    return Slot(redis, ticket, pool, held)
            if deadline is not None and time.monotonic() >= deadline:
                await abandon()
                return None
            if give_up is not None and await give_up():
                await abandon()
                return None
            if on_queued is not None and not told:
                told = True
                await on_queued(ahead)
            await asyncio.sleep(POLL_S)
    except asyncio.CancelledError:
        # Whoever was waiting is gone: give the place up now rather than when
        # it lapses, so those behind are not held back for nothing.
        await asyncio.shield(abandon())
        raise


class Lease:
    """One at a time under ``key``, in this process and in Valkey: one at a
    time across the backend's processes, not merely inside one of them.

    This is what a seat and a room are serialized by. The ``asyncio.Lock`` it
    replaces could not see the other process — a turn that landed on the
    process not holding the lock queued behind a lock that was never going to
    be released on that side.

    ``async with lease:`` waits for the process's lock and then for the key.
    Both, always, and in that order: the lock is what orders the turns of one
    process at no cost, and the key is what orders the processes. A take that
    had only the key — because this process's lock was busy with a turn that
    itself could not reach Valkey — would be a seat taken twice, since that
    turn is inside without a key anyone can see. A take that had only the lock
    is not a seat at all once there are two processes, which is the bug this
    replaces. So the lock is taken first and the key under it, and by the time
    a turn is inside it holds both.

    Three things about it are not what an ``asyncio.Lock`` was:

    - **Not re-entrant by itself.** A task taking the same seat twice waits
      for itself. ``seat_admission`` is what a turn's nested admission goes
      through, and its ``ContextVar`` is what makes that yield instead.
    - **No order between processes.** The hold is a ``SET NX PX`` and a waiter
      looks again every ``POLL_S``: whoever gets there first wins. Inside one
      process the lock underneath still hands the seat to the waiter that has
      been waiting longest, as a bare ``asyncio.Lock`` did; it is only the
      waiting between processes, which never happened before this change, that
      comes without an order.
    - **A holder that dies without releasing is waited out.** A cancelled turn
      lets the seat go at once, as before, but a process killed outright keeps
      it until the hold runs out, ``LEASE_S`` (30 s). Only the crash path
      differs. The lock lets go with the process, and is not what the other
      process waits on.
    """

    def __init__(self, key: str) -> None:
        self.key = key
        #: What orders the turns of this process. Let go by ``__aexit__``,
        #: which is the frame that also has the Valkey hold to let go.
        self._lock = asyncio.Lock()
        #: The hold each turn inside the seat is inside with, one per take,
        #: innermost last. The lock is let go by the holder of the lock; the
        #: hold belongs to the caller and has to be handed to ``__aexit__``.
        self._held: list[Slot | None] = []

    def locked(self) -> bool:
        """Whether THIS process holds the seat now. Another process holding it
        is what ``async with`` waits for and is not what this answers: the
        question is the one an ``asyncio.Lock`` answered, and it still answers
        it the same way.

        Read from what is held rather than from the lock, which is held from
        the moment a take here starts waiting, key and all. A turn of this
        process queued behind another process's turn does not hold the seat,
        and a caller refused on this being true would be told to wait for a
        turn of its own that is not running. ``_held`` is appended when the
        take is inside and popped before the seat is let go, so it is the
        seat, not the waiting for it.
        """
        return bool(self._held)

    async def __aenter__(self) -> "Lease":
        # Not `async with self._lock`: the lock has to outlive this call, and
        # be let go by `__aexit__`, next to the hold it was taken for.
        await self._lock.acquire()
        try:
            slot = await self._take()
        except BaseException:
            # Cancelled or broken between the seat here and the seat in
            # Valkey. Whoever is next does not wait for a turn that never
            # began, and the key this took is dropped by `_take` itself.
            self._lock.release()
            raise
        self._held.append(slot)
        return self

    async def __aexit__(self, *_exc: object) -> None:
        slot = self._held.pop()
        try:
            if slot is not None:
                # Let the seat go even when the turn holding it was cancelled,
                # or it stays taken for LEASE_S for nothing: the same reason
                # `enter` shields its own release.
                await asyncio.shield(slot.release())
        finally:
            # And the lock goes whatever happened above: a turn that ends has
            # to leave the seat free here, or every later turn of this process
            # waits for it for good.
            self._lock.release()

    async def _take(self) -> Slot | None:
        """The Valkey hold, or None when there is no Valkey to hold it in.

        Valkey being unreachable costs the cross-process half and nothing
        else: the seat is what it was before there was a lease, and the next
        take asks Valkey again. What this take wrote before it failed is taken
        back, or the seat reads as taken for ``LEASE_S`` in every process
        while this one goes on without it.
        """
        redis = get_redis_client()
        if redis is None:
            return None
        ticket = str(uuid.uuid4())
        try:
            return await enter(redis, ticket, hold=Hold(self.key))
        except (RedisError, OSError) as failure:
            logger.warning(
                "seat %s unheld in Valkey (%s); holding it in this process",
                self.key,
                failure,
            )
            # By this ticket's own value, so a hold another turn took is not
            # touched.
            try:
                await redis.eval(_DROP_HOLD, 1, self.key, ticket)  # type: ignore[misc]
            except (RedisError, OSError):
                logger.warning(
                    "seat %s left held in Valkey; it lapses by itself",
                    self.key,
                )
            return None


async def holding(redis: Redis, pool: Pool, tickets: list[str]) -> set[str]:
    """Which of these tickets hold a place in ``pool`` now."""
    holders = pool.keys()[0]
    scores = [await redis.zscore(holders, ticket) for ticket in tickets]
    now = time.time() * 1000
    return {
        ticket
        for ticket, score in zip(tickets, scores, strict=True)
        if score is not None and score > now
    }


async def queued(redis: Redis | None, pool: Pool) -> int:
    """How many are waiting for a place in ``pool``."""
    if redis is None:
        return 0
    return int(await redis.zcard(pool.keys()[1]))
