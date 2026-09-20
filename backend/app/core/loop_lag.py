"""How late this process's event loop is running — measured, not inferred.

Everything the backend does shares one event loop, so any stretch of work that
does not await — a large JSON encode, a synchronous library call — stops every
other request for as long as it runs. What that looks like from outside is not
「the loop was busy」 but whatever timed out first: on 2026-09-19 it was 220
`Timeout reading from 192.168.16.6:6379` in ten minutes, on a box whose load
was 3 and whose Valkey answered its slowest command in 187 ms. Nothing in the
process could say whether the loop had stalled, so the alert named Redis and
the cause stayed open.

The measurement is the one every asyncio deployment uses, because there is no
cheaper honest one: a task sleeps a fixed interval and reports how much later
than that it actually woke. Sleeping 0.25 s and waking 3 s later means the loop
spent 2.75 s inside something that never yielded.

What this does NOT say is what that something was. Nothing short of a profiler
can: `py-spy dump --nonblocking --pid 1` inside the container (needs
`SYS_PTRACE`) prints every thread's stack at the moment it is stuck, which is
the next step when a stall here says there is something to look for.
"""

import asyncio
import time

from app.core.obs import get_logger

logger = get_logger("cheesex.loop")

# How often the watchdog wakes. Short enough to catch a stall of about a second,
# long enough that the measurement itself is nothing: 4 wake-ups a second.
INTERVAL_S = 0.25
# A stall worth a line. Not the 100 ms an idle service can be held to — this
# process runs an agent platform, and a hundred milliseconds of JSON is normal
# here. A second is already long enough to be the reason a request failed.
STALL_S = 1.0
# One line a minute at most, for the same reason the pool-saturation warning is
# rate-limited: a process that is stalling repeatedly must not also flood.
REPORT_EVERY_S = 60.0

_worst = 0.0
_recent = 0.0


def lag_status() -> dict[str, float]:
    """The last measured lag and the worst since this process started, in ms."""
    return {"recent_ms": round(_recent * 1000, 1), "worst_ms": round(_worst * 1000, 1)}


async def watch_loop_lag(
    *,
    interval_s: float = INTERVAL_S,
    stall_s: float = STALL_S,
    report_every_s: float = REPORT_EVERY_S,
    iterations: int | None = None,
) -> None:
    """Sleep, measure how late that sleep returned, say so when it is far too late."""
    global _worst, _recent
    loop = asyncio.get_running_loop()
    reported_at = 0.0
    while iterations is None or iterations > 0:
        if iterations is not None:
            iterations -= 1
        began = loop.time()
        await asyncio.sleep(interval_s)
        lag = loop.time() - began - interval_s
        _recent = lag
        _worst = max(_worst, lag)
        if lag < stall_s:
            continue
        now = time.monotonic()
        if now - reported_at < report_every_s:
            continue
        reported_at = now
        logger.warning(
            "event loop stalled",
            stalled_ms=round(lag * 1000),
            worst_ms=round(_worst * 1000),
            note=(
                "one piece of work held the loop this long; every other request "
                "waited. py-spy dump --nonblocking --pid 1 names it."
            ),
        )
