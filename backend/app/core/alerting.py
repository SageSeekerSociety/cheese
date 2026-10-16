"""Tell a person, out of band, when something broke that nobody is watching.

Everything the platform notices already lands somewhere queryable — a 现场 event
block, a log line, a system event in a room. That is enough for whoever is
looking, and worth nothing when nobody is. A frontend error sits in the timeline
of a room that may have no one in it; a user sees 「操作失败」 and moves on.

So: one webhook, and a rule about what deserves it.

**Not every error.** A render loop throws hundreds of times a second, and a
broken deploy makes every page fail at once. A channel that receives all of that
is muted by the end of the first day, and a muted alert is worse than none —
it reads as coverage. What goes out is the FIRST occurrence of something, and
what does not is every repeat; the count is what a digest is for.

Disabled by default: with no webhook configured this module is a no-op that
still costs nothing to call, so the call sites do not grow an `if`.
"""

import asyncio
import hashlib
import logging
import time
from datetime import datetime

import httpx

from app.core.config import settings
from app.core.redis import get_redis_client

logger = logging.getLogger(__name__)

# Even first-occurrences burst: a bad deploy makes many DIFFERENT errors at once.
# Past this many in a window the channel is told once that it is being flooded,
# and the rest are left to the timeline and the digest.
MAX_PER_WINDOW = 10
WINDOW_S = 300.0
# How long one distinct failure stays reported. The paragraph above promises the
# first occurrence and not the repeats, and for the first day this module ran
# nothing enforced it: 600 messages in 15 hours, of which the largest single
# family was one condition — a device being off — restated 162 times. A poller
# that runs every 60 seconds against a machine somebody closed for the night
# will do that, and the ten-per-five-minutes budget below then spends itself on
# the repetition, so a NEW failure arriving during it is the one that gets
# dropped. Suppressing the repeat is therefore not tidiness; it is what keeps
# the budget meaning "ten different problems".
#
# The window grows each time the same failure is reported again while it is
# still going on. A fixed hour was not enough: a room cleanup that retried every
# 61 minutes against a device that stayed off for three days was reported 40
# times (2026-10-13..15), every retry landing just past the hour. Once it has
# been told twice the reader knows it is ongoing; a third report four hours
# later and then one a day says it still is, without being the wallpaper that
# buried the 2026-10-15 outage alert.
REPEAT_WINDOWS_S = (3600.0, 4 * 3600.0, 24 * 3600.0)
# A failure that has not come back for this many times its current window is
# over; the next occurrence is news again and starts from the first window.
QUIET_FACTOR = 2
# The repeat state lives in Redis, not in this process: the backend is
# restarted by every deploy, several times a day, and a process-local memory
# made every ongoing failure report again from scratch after each one (the
# same cleanup came back 1, 5, 10 and 18 minutes after its previous report).
REPEAT_KEY_PREFIX = "cheese:alert:repeat:"
# A Redis that does not answer within this long is treated as absent: the
# alert goes out rather than waiting, because an alert held up behind a broken
# Redis is the outage nobody hears about. The budget below still bounds it.
REDIS_TIMEOUT_S = 1.0
# How long this process answers a repeat of a key it has just asked Redis about
# by itself, counting it instead of asking again. Without it every occurrence
# was its own Redis round trip, and a failure logged a hundred times in one
# tick opened a hundred connections at once from the shared pool. A minute is
# far below the shortest repeat window (an hour), so nothing that would have
# been reported is held back; the count goes along with the next question.
LOCAL_GATE_S = 60.0
# Keys this process remembers at once for that gate; past it, the expired ones
# are forgotten.
LOCAL_GATE_MAX = 512
# A webhook that is slow must not hold anything up; it is told or it is not.
TIMEOUT_S = 5.0


class _Budget:
    """How many alerts this process may still send in the current window."""

    def __init__(self) -> None:
        self._sent: list[float] = []
        self._announced_flood_at: float | None = None

    def take(self, now: float) -> str:
        """``"send"``, ``"flood"`` (say so once), or ``"drop"``."""
        cutoff = now - WINDOW_S
        self._sent = [t for t in self._sent if t > cutoff]
        if len(self._sent) < MAX_PER_WINDOW:
            self._sent.append(now)
            return "send"
        if self._announced_flood_at is None or self._announced_flood_at <= cutoff:
            self._announced_flood_at = now
            return "flood"
        return "drop"


budget = _Budget()


# One atomic step per occurrence, so two processes (blue and green during a
# release) that hit the same failure at once still report it once.
# Returns -1 for a repeat to keep quiet about, otherwise how many repeats were
# kept quiet since the last report (0 = first time anyone hears of it).
_REPEAT_SCRIPT = """
local now = tonumber(ARGV[1])
local quiet = tonumber(ARGV[2])
local carried = tonumber(ARGV[3])
local windows = {}
for i = 4, #ARGV do windows[#windows + 1] = tonumber(ARGV[i]) end
local last = tonumber(redis.call('HGET', KEYS[1], 'last'))
local level = tonumber(redis.call('HGET', KEYS[1], 'level')) or 1
if last and now - last >= windows[level] * quiet then
  -- Over: the expiry below would have dropped it; say so in the clock the
  -- caller gave rather than trusting when Redis gets round to expiring it.
  redis.call('DEL', KEYS[1])
  last = nil
  level = 1
end
if not last then
  -- Counted under an episode that is over: it is not this one's repeats.
  carried = 0
end
if last and now - last < windows[level] then
  redis.call('HINCRBY', KEYS[1], 'suppressed', 1 + carried)
  return -1
end
local repeats = (tonumber(redis.call('HGET', KEYS[1], 'suppressed')) or 0) + carried
local next_level = 1
if last then next_level = math.min(level + 1, #windows) end
redis.call('HSET', KEYS[1], 'last', now, 'level', next_level, 'suppressed', 0)
redis.call('EXPIRE', KEYS[1], math.ceil(windows[next_level] * quiet))
return repeats
"""


async def _repeats(key: str, now: float, carried: int = 0) -> int | None:
    """How many repeats to mention (0 = first time anyone hears of it), or
    ``None`` when this is a repeat inside its window and must not go out.

    Keyed by whatever the caller says makes two reports the same problem, not by
    the message text: the text carries the room and the device that happened to
    hit it, and one broken thing on seventy machines is one broken thing.
    """
    client = get_redis_client()
    if client is None:
        return 0
    digest = hashlib.sha256(key.encode()).hexdigest()[:32]
    try:
        answer = await asyncio.wait_for(
            client.eval(
                _REPEAT_SCRIPT,
                1,
                f"{REPEAT_KEY_PREFIX}{digest}",
                now,
                QUIET_FACTOR,
                carried,
                *REPEAT_WINDOWS_S,
            ),
            REDIS_TIMEOUT_S,
        )
    except Exception:  # noqa: BLE001 — a broken Redis must not silence alerts
        logger.warning("alert repeat state unavailable; sending", exc_info=True)
        return 0
    answer = int(answer)
    return None if answer < 0 else answer


def _clock(ts: float) -> str:
    """When it happened, with the zone spelled out.

    The message's own timestamp is when Feishu accepted it, and the two are not
    the same number: this send is queued behind an event loop, a repeat is
    reported hours after it started, and the backend keeps UTC while the
    person reading keeps whatever their phone says. So the alert carries the
    moment the error was logged, and names the zone it is in.
    """
    return datetime.fromtimestamp(ts).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")


def configured() -> bool:
    return bool(settings.feishu_alert_webhook.strip())


async def _post(text: str) -> None:
    await _post_to(settings.feishu_alert_webhook.strip(), text)


async def _post_to(url: str, text: str) -> None:
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_S) as client:
            response = await client.post(
                url, json={"msg_type": "text", "content": {"text": text}}
            )
        # Feishu answers 200 with a body that says whether it accepted; a wrong
        # or revoked webhook is a 200 with a non-zero code, which is exactly the
        # failure someone needs to see rather than have swallowed.
        body = response.json() if response.content else {}
        if response.status_code != 200 or body.get("code") not in (None, 0):
            logger.warning("alert webhook refused: %s %s", response.status_code, body)
    except Exception:  # noqa: BLE001 — an alert that fails must not fail its caller
        logger.exception("alert webhook failed")


def send(
    title: str,
    lines: list[str],
    *,
    key: str | None = None,
    when: float | None = None,
) -> None:
    """Fire-and-forget. Never raises, never blocks the caller.

    Not awaited on purpose: every caller is on a request path or an event loop
    that has something better to do, and an alert nobody is waiting for must not
    add its round trip to a user's request.

    ``key`` is what makes two reports the same problem; the title is used when
    the caller has nothing better, which is right for a title that already names
    the thing and wrong for one carrying an id. ``when`` is when it happened,
    which is not when this runs for anything queued or replayed.
    """
    if not configured():
        return
    now = time.time()
    key = key or title
    gate = _asked.get(key)
    if gate is not None and now < gate[0]:
        gate[1] += 1
        return
    carried = gate[1] if gate is not None else 0
    if len(_asked) >= LOCAL_GATE_MAX:
        for stale in [k for k, (until, _) in _asked.items() if until <= now]:
            del _asked[stale]
    _asked[key] = [now + LOCAL_GATE_S, 0]
    try:
        task = asyncio.create_task(
            _deliver(title, lines, key, now if when is None else when, now, carried)
        )
    except RuntimeError:
        # No running loop (a sync context, a test). Nothing to alert from here.
        logger.debug("alert dropped: no running event loop")
        return
    _running.add(task)
    task.add_done_callback(_running.discard)
    task.add_done_callback(_delivery_finished)


async def _deliver(
    title: str,
    lines: list[str],
    key: str,
    happened: float,
    now: float,
    carried: int = 0,
) -> None:
    """Decide whether this occurrence is news, and post it if it is.

    Repeats are decided before the budget is spent, so a failure that keeps
    recurring cannot use up the room a different one needs.
    """
    repeats = await _repeats(key, now, carried)
    if repeats is None:
        return
    verdict = budget.take(now)
    if verdict == "drop":
        return
    stamp = f"时间：{_clock(happened)}"
    if verdict == "flood":
        text = (
            f"{title}\n{stamp}\n（同一时间还有更多新错误，已超过 {MAX_PER_WINDOW} 条/"
            f"{int(WINDOW_S / 60)} 分钟的上限，其余只记在时间线里）"
        )
    else:
        body = [title, stamp, *lines]
        if repeats:
            body.append(f"（上一条之后这个问题又发生了 {repeats} 次，仍在持续）")
        text = "\n".join(body)
    await _post(text)


def post(url: str, text: str) -> None:
    """Fire-and-forget one text message to another group's custom-bot webhook.

    For a channel that is not the error channel and so is not an alert: none of
    the budget or repeat suppression above applies, because each call is one
    distinct event the caller has already decided is worth a message (a new
    feedback report, `feedback/announce.py`). What does apply is the rest of
    this module's contract — it never raises, never blocks the caller, and a
    webhook that refuses or fails is logged here and goes no further.
    """
    try:
        task = asyncio.create_task(_post_to(url, text))
    except RuntimeError:
        logger.debug("webhook message dropped: no running event loop")
        return
    _running.add(task)
    task.add_done_callback(_running.discard)
    task.add_done_callback(_delivery_finished)


def _delivery_finished(task: asyncio.Task) -> None:
    """Say when an alert did not get out.

    Deliberately NOT `background.hold`: that logs under `cheesex.background`,
    which `obs.AlertOnError` picks up and turns into another alert — a webhook
    that is down would then post about failing to post, forever. This logger is
    named `app.core.alerting`, the one name that handler skips, so the failure
    reaches the log file and stops there.
    """
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error("alert delivery failed", exc_info=exc)


# Per key: until when a repeat is counted here rather than asked of Redis, and
# how many were counted (see LOCAL_GATE_S). Owned by this module for the life
# of the process; nothing resets it, which is why a test that sends the same
# key twice has to give itself a fresh one.
_asked: dict[str, list] = {}

# Keep a reference so the task is not garbage-collected mid-flight.
_running: set[asyncio.Task] = set()
