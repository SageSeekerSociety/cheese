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
import gc
import os
import signal
import threading
import time
from collections import Counter, deque
from datetime import UTC, datetime
from types import FrameType

try:
    from greenlet import getcurrent
except ImportError:  # pragma: no cover - SQLAlchemy's asyncio needs it
    getcurrent = None

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
# The collector's passes, per generation: how many, how long in all and at
# worst, and how many objects they freed in all and in the slowest pass. A stall whose stack is only a weakref callback (`weakref.py remove`)
# is the collector freeing what died, and `gc_ms` on the stall says how much
# of it the collector took.
_gc_runs = [[0, 0.0, 0.0, 0, 0] for _ in range(3)]
# When the pass in flight began; None between passes, so a "stop" whose
# "start" this callback never saw is not charged.
_gc_started: float | None = None
_gc_since_beat = 0.0
# Whether a watcher is sampling: the alarm handler re-arms only while it is,
# so an alarm handled after the watcher stopped cannot start another one.
_active = False


def stall_status() -> dict:
    """The stalls kept so far: how many there were, the worst and the latest."""
    return {
        # False where nothing measures (no watcher, or one off the main
        # thread): a count of 0 then says nothing about the loop.
        "active": _active,
        "threshold_ms": round(STALL_S * 1000),
        "count": _count,
        "worst": list(_worst),
        "recent": list(reversed(_recent)),
        # What decides how often the middle generation is promoted: the same
        # number the server sets at startup (`core/gc_tuning`). Beside the
        # pass counts above it says whether the tuning reached this process.
        "thresholds": list(gc.get_threshold()),
        "gc": [
            {
                "generation": generation,
                "collections": runs,
                "total_ms": round(total * 1000, 1),
                "worst_ms": round(worst * 1000, 1),
                # A full pass's time is the walk and the freeing both: with
                # how many it freed beside it, the two can be told apart.
                "collected": collected,
                "worst_collected": worst_collected,
            }
            for generation, (
                runs,
                total,
                worst,
                collected,
                worst_collected,
            ) in enumerate(_gc_runs)
        ],
    }


def _where(frame: FrameType | None) -> tuple[str, ...]:
    """The stack from ``frame`` outwards. The backend's own frames are kept,
    and the one frame of anything else that the innermost of them called (the
    library call holding the loop); the frames between are left out.

    SQLAlchemy runs a statement's result handling (rows, JSON decoding) in a
    greenlet of its own, whose frames end at the greenlet: the stack carries
    on in the greenlet that switched to it, where the backend's frames are.

    When the loop is held inside a library that has no backend frame of its own
    anywhere below the innermost one — a socket read in `ssl`, a signal handled
    while `socket.recv` waits — every frame is a library frame. Keeping only the
    innermost then left a one-frame stack that named the library and not the
    caller: production's stalls in `ssl.py read` could not say which of the
    backend's calls was waiting. Those frames are kept, folded into one entry,
    until the first backend frame is reached."""
    out: list[str] = []
    library: list[str] = []
    glet = getcurrent() if getcurrent is not None else None
    while len(out) < DEPTH:
        if frame is None:
            glet = glet.parent if glet is not None else None
            if glet is None:
                break
            frame = glet.gr_frame
            continue
        code = frame.f_code
        path = code.co_filename
        if path.startswith(_APP):
            if library:
                # One entry for the library calls between two backend frames:
                # the innermost names the call holding the loop, the rest say
                # how deep it sits.
                out.append(" <- ".join(library))
                library.clear()
            out.append(f"app/{path[len(_APP) :]}:{frame.f_lineno} {code.co_name}")
        elif not out:
            short = path.rsplit("site-packages/", 1)[-1].rsplit("/lib/python", 1)[-1]
            library.append(f"{short}:{frame.f_lineno} {code.co_name}")
        frame = frame.f_back
    if library and len(out) < DEPTH:
        out.append(" <- ".join(library))
    return tuple(out)


def _on_gc(phase: str, info: dict) -> None:
    global _gc_started, _gc_since_beat
    if phase == "start":
        _gc_started = time.perf_counter()
        return
    if _gc_started is None:
        return
    took = time.perf_counter() - _gc_started
    _gc_started = None
    _gc_since_beat += took
    runs = _gc_runs[min(info.get("generation", 2), 2)]
    freed = info.get("collected", 0)
    runs[0] += 1
    runs[1] += took
    runs[3] += freed
    if took >= runs[2]:
        runs[2] = took
        runs[4] = freed


def _on_alarm(signum: int, frame: FrameType | None) -> None:
    if not _active:
        return
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
        "gc_ms": round(_gc_since_beat * 1000),
        "stack": list(stack),
    }
    _count += 1
    _recent.append(stall)
    _worst.append(stall)
    _worst.sort(key=lambda s: -s["ms"])
    del _worst[KEEP:]


async def watch_loop_stalls(*, iterations: int | None = None) -> None:
    """Beat on the loop and keep where it was each time a beat came late."""
    global _active, _gc_since_beat
    if threading.current_thread() is not threading.main_thread():
        return
    previous = signal.getsignal(signal.SIGALRM)
    if previous is not signal.SIG_DFL:
        # Someone else's alarm (a Python handler, or None: one installed
        # outside Python); not ours to take.
        return
    signal.signal(signal.SIGALRM, _on_alarm)
    signal.siginterrupt(signal.SIGALRM, False)
    _active = True
    gc.callbacks.append(_on_gc)
    try:
        while iterations is None or iterations > 0:
            if iterations is not None:
                iterations -= 1
            began = time.monotonic()
            _gc_since_beat = 0.0
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
        _active = False
        if _on_gc in gc.callbacks:
            gc.callbacks.remove(_on_gc)
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
