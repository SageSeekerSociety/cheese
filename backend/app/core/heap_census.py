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
(`/admin/stats/performance?heap=true`), never on a timer. Asked with
`&owners=true`, it also traces who holds a sample of the leading types, in
two more passes over the heap: about five times as long (610,000 objects:
710 ms against 140 ms on the dev box).
"""

import gc
import time
from collections import Counter

TOP = 40
# With owners: how many of each of the top types to trace, and how many types.
SAMPLE = 200
TRACED_TYPES = 6
_CONTAINERS = (dict, list, tuple, set, frozenset)


def _name(kind: type) -> str:
    return f"{kind.__module__}.{kind.__qualname__}"


def _holders(objects: list, wanted: dict[int, object]) -> dict[int, list]:
    """For each object in ``wanted`` (by id), the objects that refer to it,
    found in one pass over ``objects``."""
    found: dict[int, list] = {key: [] for key in wanted}
    for obj in objects:
        for ref in gc.get_referents(obj):
            held = found.get(id(ref))
            if held is not None and len(held) < 4:
                held.append(obj)
    return found


def _owners(objects: list, by_type: Counter) -> list[dict]:
    """Who holds the top types: for a sample of each, the type of each object
    that refers to it, and for a plain container, the type of what holds that
    container in turn."""
    traced = [kind for kind, _ in by_type.most_common(TRACED_TYPES)]
    samples: dict[type, dict[int, object]] = {kind: {} for kind in traced}
    for obj in objects:
        bucket = samples.get(type(obj))
        if bucket is not None and len(bucket) < SAMPLE:
            bucket[id(obj)] = obj
    wanted = {key: obj for bucket in samples.values() for key, obj in bucket.items()}
    wanted_ids = {id(wanted), id(objects), *(id(b) for b in samples.values())}
    first = _holders(objects, wanted)
    containers = {
        id(holder): holder
        for holders in first.values()
        for holder in holders
        if type(holder) in _CONTAINERS and id(holder) not in wanted_ids
    }
    second = _holders(objects, containers)
    report = []
    for kind in traced:
        counts: Counter[str] = Counter()
        for key in samples[kind]:
            for holder in first[key]:
                if id(holder) in wanted_ids:
                    continue
                path = _name(type(holder))
                if type(holder) in _CONTAINERS:
                    above = [
                        _name(type(h))
                        for h in second.get(id(holder), [])
                        if id(h) not in wanted_ids
                    ]
                    path += " < " + (above[0] if above else "?")
                counts[path] += 1
        report.append(
            {
                "type": _name(kind),
                "sampled": len(samples[kind]),
                "held_by": [
                    {"path": path, "count": n} for path, n in counts.most_common(6)
                ],
            }
        )
    return report


def heap_census(top: int = TOP, *, owners: bool = False) -> dict:
    """How many objects the collector tracks outside the frozen startup heap,
    how many are frozen, and the ``top`` types by count; with ``owners``,
    also who holds a sample of the leading types (two more passes over the
    heap, so several times as long)."""
    started = time.perf_counter()
    objects = gc.get_objects()
    # Counted by the type object in C (`map`, `Counter`'s own loop), named
    # only for the few hundred distinct types: naming each object as it went
    # took five times as long as a full collection.
    by_type = Counter(map(type, objects))
    tracked = len(objects)
    held_by = _owners(objects, by_type) if owners else None
    del objects
    census = {
        "tracked": tracked,
        "frozen": gc.get_freeze_count(),
        "took_ms": round((time.perf_counter() - started) * 1000, 1),
        "top": [
            {"type": _name(kind), "count": n} for kind, n in by_type.most_common(top)
        ],
    }
    if held_by is not None:
        census["owners"] = held_by
    return census
