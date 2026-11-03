"""Let the middle generation fill before the collector promotes it.

A full collection (generation 2) runs on the event loop's thread with the loop
held, and its cost is the size of the whole tracked heap, not the garbage it
finds. Production's records (`core/loop_stalls`) show what that costs at the
app's size: over 2.6 hours, 30 full passes in 6.3 s at about 208 ms each
(worst 397 ms), freeing about 6,300 objects per pass — 2.4% of the 260,000
objects they walked. All ten of the worst stalls the board kept in that window
have the collector's own time (`gc_ms`) at least as long as the stall itself.

CPython 3.13 decides that frequency in two steps (`Python/gc.c`,
`gc_select_generation`). Generation 0's count passes its threshold, its
survivors are merged into generation 1; generation 1's count passes its
threshold, its survivors merge into generation 2 and, only if they make up a
quarter of generation 2, a full pass runs. At the generation-1 default of 10
the middle generation fills after ten gen-0 passes and promotes far more often
than that quarter allows, so a full pass runs most times the middle one fills.
Most of what fills it is short-lived: a request's rows and the traceback of a
caught exception, which only the collector can free. The heap those objects are
promoted into is the long-lived one — statement caches, compiled trees — and
that is what the pass then walks.

Holding the middle generation longer moves fewer objects into the old one per
promotion, so the quarter gate holds more often and full passes happen less
often. On a heap of production's shape with a fixed amount of work (120,000
request-sized units, two interleaved rounds), raising the gen-1 threshold from
10 to 100 took full passes from 5 to 3 and their total from 527 ms to 299 ms;
at 200 it took them to 1 and 100 ms. The garbage they did not free stayed in
generation 1 and was freed there instead — the peak pile of undead cycles was
the same under all three (92,403 objects).

Only the server's processes get this. A test suite, a script or a worker runs
the interpreter's defaults: the numbers above come from the app's steady state,
and changing what every process does with its garbage makes every unrelated
measurement incomparable.
"""

import gc
import os
import threading

SERVING = "CHEESE_SERVING"

#: Generation 1's allocation threshold; 0 and 2 keep whatever this interpreter
#: started with. Only this number is ours to set: generation 0's default has
#: already moved between builds of 3.13 (700, then 2000), and pinning it here
#: would silently make the young generation collect more often than the build
#: we run chose to.
GEN1_THRESHOLD = 100

_done = False


def tune_collector(*, serving: bool, on_main_thread: bool) -> bool:
    """Raise the middle generation's threshold in a serving process. Whether it
    did."""
    global _done
    if _done or not serving or not on_main_thread:
        return False
    gen0, _gen1, gen2 = gc.get_threshold()
    gc.set_threshold(gen0, GEN1_THRESHOLD, gen2)
    _done = True
    return True


def tune_after_startup() -> bool:
    """`tune_collector` for this process: only one the server's entry point
    started (`app/serve.py` sets `CHEESE_SERVING`), on its main thread."""
    return tune_collector(
        serving=os.environ.get(SERVING) == "1",
        on_main_thread=threading.current_thread() is threading.main_thread(),
    )
