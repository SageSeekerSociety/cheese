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

When the loop is stuck for a second, a C watchdog writes every Python thread's
stack to stderr, including the frame that held the loop. For an on-demand
snapshot later, py-spy, which the image carries, prints every thread's stack.
uvicorn is not PID 1 (`init: true` puts tini there) but tini's first child, and
reading another process's memory takes `SYS_PTRACE`, which the compose file
grants and only root in the container holds:

    docker exec -u 0 <backend container> sh -c \\
      'py-spy dump --nonblocking --pid "$(cut -d" " -f1 /proc/1/task/1/children)"'
"""

import asyncio
import faulthandler
import sys
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
# One diagnostic a minute at most: repeated stalls must not flood stderr.
REPORT_EVERY_S = 60.0

DUMP_FILE = sys.stderr

_worst = 0.0
_recent = 0.0
_last_dump_at: float | None = None


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
    global _last_dump_at, _worst, _recent
    loop = asyncio.get_running_loop()
    timer_armed = False
    try:
        while iterations is None or iterations > 0:
            if iterations is not None:
                iterations -= 1
            should_report = (
                _last_dump_at is None
                or time.monotonic() - _last_dump_at >= report_every_s
            )
            timer_started_at: float | None = None
            if should_report:
                try:
                    faulthandler.dump_traceback_later(stall_s, file=DUMP_FILE)
                    timer_armed = True
                    timer_started_at = time.monotonic()
                except (AttributeError, OSError, RuntimeError, ValueError):
                    # Captured streams and closed files may not expose a usable fd.
                    pass
            began = loop.time()
            timer_fired = False
            try:
                await asyncio.sleep(interval_s)
            finally:
                if timer_armed:
                    timer_fired = (
                        timer_started_at is not None
                        and time.monotonic() - timer_started_at >= stall_s
                    )
                    faulthandler.cancel_dump_traceback_later()
                    timer_armed = False
            lag = loop.time() - began - interval_s
            _recent = lag
            _worst = max(_worst, lag)
            now = time.monotonic()
            if timer_fired:
                _last_dump_at = now
            if (lag < stall_s and not timer_fired) or not should_report:
                continue
            _last_dump_at = now
            logger.warning(
                "event loop stalled",
                stalled_ms=round(lag * 1000),
                worst_ms=round(_worst * 1000),
                note=(
                    "one piece of work held the loop this long; every other request "
                    "waited. A stack dump is written when stderr exposes "
                    "a usable file descriptor."
                ),
            )
    finally:
        if timer_armed:
            faulthandler.cancel_dump_traceback_later()
