"""Walk the young heap less often, and let the middle generation fill.

Two thresholds are raised, both above the interpreter's default. Generation
0's decides how much each pass walks; it is the collector's largest cost —
about 11,300 passes for 26 s in a 6.2-hour production window — but not the
largest stall, because a pass is 2.3 ms. It is held as a floor at `GEN0_THRESHOLD`,
never below what the build already does. Generation 1's decides how often
survivors are promoted, and through that how often a full pass runs; that one
is what the rest of this docstring argues.

A full collection (generation 2) runs on the event loop's thread with the loop
held, and its cost is the size of the whole tracked heap, not the garbage it
finds. Production's records (`core/loop_stalls`) show what that costs at the
app's size: over 2.6 hours, 30 full passes in 6252 ms at about 208 ms each
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

# The entry point's mark, from the module that already owns it: a copy here
# would drift from `app/startup_freeze`'s without any test noticing, and the
# tuning would stop reaching production with nothing failing.
from app.core.startup_freeze import SERVING

#: Generation 0's allocation threshold (a floor — see `tune_collector`). It
#: decides how much each pass walks, and a pass costs about the same per object
#: whether it walks 2,000 or 10,000 of them — so raising it trades passes for
#: time per pass and frees less often, but it does not by itself remove the
#: collector's cost. Production, 6.2 h at 2,000: ~11,300 passes for 26 s — 83%
#: of all the time the collector took, 2.3 ms each, freeing ~917 objects each.
#: At 5,000 the same work is ~4,500 passes of ~5.8 ms. What the raise really
#: buys is fewer promotions: the young heap fills slower, so the full passes
#: that hold the loop for 200 ms apiece fall from 10 to about 4 in that window.
#: On a heap of production's shape the extra young objects held between passes
#: moved neither the peak object count nor RSS.
GEN0_THRESHOLD = 5000

#: Generation 1's allocation threshold; generation 2 keeps whatever this
#: interpreter started with. Unlike the generation-0 floor above, this value is
#: set outright rather than floored: it has been 10 on every CPython release so
#: far, so there is no build whose own choice it could tune down.
GEN1_THRESHOLD = 100

_done = False


def tune_collector(*, serving: bool, on_main_thread: bool) -> bool:
    """Raise the middle generation's threshold in a serving process. Whether it
    did."""
    global _done
    if _done or not serving or not on_main_thread:
        return False
    gen0, _gen1, gen2 = gc.get_threshold()
    # A floor, not a setting: the interpreter's own value is kept wherever it
    # is already higher. The default has moved before (700 through 3.12, 2000
    # from 3.13) and may move again; a build that ships 10,000 would otherwise
    # be tuned *down* here, collecting the young heap more often than it chose.
    gc.set_threshold(max(gen0, GEN0_THRESHOLD), GEN1_THRESHOLD, gen2)
    _done = True
    return True


def tune_after_startup() -> bool:
    """`tune_collector` for this process: only one the server's entry point
    started (`app/serve.py` sets `CHEESE_SERVING`), on its main thread."""
    return tune_collector(
        serving=os.environ.get(SERVING) == "1",
        on_main_thread=threading.current_thread() is threading.main_thread(),
    )
