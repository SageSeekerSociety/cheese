"""What the garbage collector walks: the live objects it tracks, by type.

A full collection (generation 2) walks every tracked object that is not
frozen, and with the event loop held: production's stall records
(`core/loop_stalls`) showed full passes of about 150 ms every few minutes, on
a process seventeen minutes old. The startup heap is frozen at import
(`main.py`), so what is walked is what the running process accumulated, and
which types those are says where to look.

Counting holds the loop a little longer than a full collection does (2 million
objects: 270 ms against 160 ms on the dev box), so it runs only when an
admin asks for it
(`/admin/stats/performance?heap=true`), never on a timer.
"""

import gc
import time
from collections import Counter

TOP = 40


def heap_census(top: int = TOP) -> dict:
    """How many objects the collector tracks outside the frozen startup heap,
    how many are frozen, and the ``top`` types by count."""
    started = time.perf_counter()
    objects = gc.get_objects()
    # Counted by the type object in C (`map`, `Counter`'s own loop), named
    # only for the few hundred distinct types: naming each object as it went
    # took five times as long as a full collection.
    by_type = Counter(map(type, objects))
    tracked = len(objects)
    del objects
    return {
        "tracked": tracked,
        "frozen": gc.get_freeze_count(),
        "took_ms": round((time.perf_counter() - started) * 1000, 1),
        "top": [
            {"type": f"{kind.__module__}.{kind.__qualname__}", "count": n}
            for kind, n in by_type.most_common(top)
        ],
    }
