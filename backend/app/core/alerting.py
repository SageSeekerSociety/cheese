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

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

# Even first-occurrences burst: a bad deploy makes many DIFFERENT errors at once.
# Past this many in a window the channel is told once that it is being flooded,
# and the rest are left to the timeline and the digest.
MAX_PER_WINDOW = 10
WINDOW_S = 300.0
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


def send(title: str, lines: list[str]) -> None:
    """Fire-and-forget. Never raises, never blocks the caller.

    Not awaited on purpose: every caller is on a request path or an event loop
    that has something better to do, and an alert nobody is waiting for must not
    add its round trip to a user's request.
    """
    if not configured():
        return
    verdict = budget.take(time.time())
    if verdict == "drop":
        return
    if verdict == "flood":
        text = (
            f"{title}\n（同一时间还有更多新错误，已超过 {MAX_PER_WINDOW} 条/"
            f"{int(WINDOW_S / 60)} 分钟的上限，其余只记在时间线里）"
        )
    else:
        text = "\n".join([title, *lines])
    try:
        task = asyncio.create_task(_post(text))
        _running.add(task)
        task.add_done_callback(_running.discard)
    except RuntimeError:
        # No running loop (a sync context, a test). Nothing to alert from here.
        logger.debug("alert dropped: no running event loop")


# Keep a reference so the task is not garbage-collected mid-flight.
_running: set[asyncio.Task] = set()
