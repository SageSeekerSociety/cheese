"""Where the event loop was when it stalled, for stalls too short to dump.

`core/loop_lag` measures how late the loop runs and, past a second, has the C
watchdog write every thread's stack to stderr. Shorter stalls leave only a
number: production showed a worst lag of 830 ms with nothing to say what held
the loop, and every request in flight waited that long.

This keeps the rest. A task on the loop beats every `BEAT_S`, and before each
sleep arms a one-shot interval timer (`ITIMER_REAL`) for `STALL_S` past the
beat's due time; the beat disarms it on waking. A loop that does not get back
to the beat in time gets `SIGALRM`, whose handler runs on the loop's thread
between two bytecodes and is handed the frame that thread is in: the stack
holding the loop. The handler re-arms every `SAMPLE_S` while the stall lasts,
and when the beat runs again the stall is kept with the stack seen most often:
the `KEEP` worst since the process started and the `KEEP` most recent, for
`/admin/stats/performance`. Process memory, like the lag itself: a restart
clears it.

Nothing runs beside the loop: no thread wakes to look (a second Python thread
waking 50 times a second slowed a busy loop by 8%), only two timer syscalls a
beat. A C call that holds the loop is seen when it returns, at the frame that
called it. Signals reach Python only on the main thread, which is where the
server runs its loop; anywhere else (a test client's loop thread) this keeps
nothing. The handler is installed with restart semantics, so a system call
another thread is blocked in resumes rather than failing with EINTR.
"""

import asyncio
import os
import signal
import threading
import time
from collections import Counter, deque
from datetime import UTC, datetime
from types import FrameType

BEAT_S = 0.05
SAMPLE_S = 0.02
# A stall worth its stack: well above what a single request's Python takes,
# well below the second `core/loop_lag` already dumps for.
STALL_S = 0.1
KEEP = 10
# Frames kept per stack, innermost first.
DEPTH = 14
# The backend's own package: frames under it are the ones a stall names.
_APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__))) + os.sep

_worst: list[dict] = []
_recent: deque[dict] = deque(maxlen=KEEP)
_count = 0
_samples: Counter[tuple[str, ...]] = Counter()


def stall_status() -> dict:
    """The stalls kept so far: how many there were, the worst and the latest."""
    return {
        "threshold_ms": round(STALL_S * 1000),
        "count": _count,
        "worst": list(_worst),
        "recent": list(reversed(_recent)),
    }


def _where(frame: FrameType | None) -> tuple[str, ...]:
    """The stack from ``frame`` outwards. The backend's own frames are kept,
    and the one frame of anything else that the innermost of them called (the
    library call holding the loop); the frames between are left out."""
    out: list[str] = []
    while frame is not None and len(out) < DEPTH:
        code = frame.f_code
        path = code.co_filename
        if path.startswith(_APP):
            short = "app/" + path[len(_APP) :]
        elif not out:
            short = path.rsplit("site-packages/", 1)[-1].rsplit("/lib/python", 1)[-1]
        else:
            frame = frame.f_back
            continue
        out.append(f"{short}:{frame.f_lineno} {code.co_name}")
        frame = frame.f_back
    return tuple(out)


def _on_alarm(signum: int, frame: FrameType | None) -> None:
    _samples[_where(frame)] += 1
    signal.setitimer(signal.ITIMER_REAL, SAMPLE_S)


def _keep(late: float, samples: Counter[tuple[str, ...]]) -> None:
    global _count
    stack, seen = samples.most_common(1)[0]
    total = sum(samples.values())
    stall = {
        "ms": round(late * 1000),
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
        "samples": total,
        "share": round(seen / total, 2),
        "stack": list(stack),
    }
    _count += 1
    _recent.append(stall)
    _worst.append(stall)
    _worst.sort(key=lambda s: -s["ms"])
    del _worst[KEEP:]


async def watch_loop_stalls(*, iterations: int | None = None) -> None:
    """Beat on the loop and keep where it was each time a beat came late."""
    if threading.current_thread() is not threading.main_thread():
        return
    if signal.getsignal(signal.SIGALRM) not in (signal.SIG_DFL, None):
        return  # someone else's alarm; not ours to take
    signal.signal(signal.SIGALRM, _on_alarm)
    signal.siginterrupt(signal.SIGALRM, False)
    try:
        while iterations is None or iterations > 0:
            if iterations is not None:
                iterations -= 1
            began = time.monotonic()
            signal.setitimer(signal.ITIMER_REAL, BEAT_S + STALL_S)
            try:
                await asyncio.sleep(BEAT_S)
            finally:
                signal.setitimer(signal.ITIMER_REAL, 0)
            late = time.monotonic() - began - BEAT_S
            if not _samples:
                continue
            samples = _samples.copy()
            _samples.clear()
            if late > STALL_S:
                _keep(late, samples)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, signal.SIG_DFL)
