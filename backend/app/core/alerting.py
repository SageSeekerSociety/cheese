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
import logging
import time
from datetime import datetime

import httpx

from app.core.config import settings

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
REPEAT_WINDOW_S = 3600.0
# Distinct failures remembered at once. Past this the oldest are forgotten — a
# forgotten one reports again, which is the failure direction to prefer.
MAX_TRACKED = 512
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


class _Repeats:
    """The first occurrence of each distinct failure, and what came back after.

    Keyed by whatever the caller says makes two reports the same problem, not by
    the message text: the text carries the room and the device that happened to
    hit it, and one broken thing on seventy machines is one broken thing.
    """

    def __init__(self) -> None:
        self._first: dict[str, float] = {}
        self._suppressed: dict[str, int] = {}

    def take(self, key: str, now: float) -> int | None:
        """How many repeats to mention (0 = first time anyone hears of it), or
        ``None`` when this is a repeat inside the window and must not go out."""
        first = self._first.get(key)
        if first is not None and now - first < REPEAT_WINDOW_S:
            self._suppressed[key] = self._suppressed.get(key, 0) + 1
            return None
        repeats = self._suppressed.pop(key, 0)
        self._first[key] = now
        self._forget_oldest()
        return repeats

    def _forget_oldest(self) -> None:
        excess = len(self._first) - MAX_TRACKED
        if excess <= 0:
            return
        for key in sorted(self._first, key=lambda k: self._first[k])[:excess]:
            del self._first[key]
            self._suppressed.pop(key, None)


repeated = _Repeats()


def _clock(ts: float) -> str:
    """When it happened, with the zone spelled out.

    The message's own timestamp is when Feishu accepted it, and the two are not
    the same number: this send is queued behind an event loop, a repeat is
    reported up to an hour after it started, and the backend keeps UTC while the
    person reading keeps whatever their phone says. So the alert carries the
    moment the error was logged, and names the zone it is in.
    """
    return datetime.fromtimestamp(ts).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")


def configured() -> bool:
    return bool(settings.feishu_alert_webhook.strip())


async def _post(text: str) -> None:
    url = settings.feishu_alert_webhook.strip()
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
    repeats = repeated.take(key or title, now)
    if repeats is None:
        return
    verdict = budget.take(now)
    if verdict == "drop":
        return
    stamp = f"时间：{_clock(now if when is None else when)}"
    if verdict == "flood":
        text = (
            f"{title}\n{stamp}\n（同一时间还有更多新错误，已超过 {MAX_PER_WINDOW} 条/"
            f"{int(WINDOW_S / 60)} 分钟的上限，其余只记在时间线里）"
        )
    else:
        body = [title, stamp, *lines]
        if repeats:
            body.append(
                f"（上一条之后这个问题又发生了 {repeats} 次，"
                f"{int(REPEAT_WINDOW_S / 60)} 分钟内只报一条）"
            )
        text = "\n".join(body)
    try:
        task = asyncio.create_task(_post(text))
        _running.add(task)
        task.add_done_callback(_running.discard)
        task.add_done_callback(_delivery_finished)
    except RuntimeError:
        # No running loop (a sync context, a test). Nothing to alert from here.
        logger.debug("alert dropped: no running event loop")


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


# Keep a reference so the task is not garbage-collected mid-flight.
_running: set[asyncio.Task] = set()
