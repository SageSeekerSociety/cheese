"""Per-endpoint HTTP timing — the spine the board's route table reads.

Why this exists next to ``core/metrics.py``: that registry keys a histogram by
``(method, route, status)``, so one endpoint under three status codes is three
series and the board could only ever show the handful it happened to sample.
The board's question is「哪条慢」per **endpoint**, one row per
``(method, route_template)``, with the status mix as an attribute rather than
an identity.

Bounded on purpose. The ring holds the last 256 samples per route (so the
percentiles are exact over a *recent window*, not lifetime — say so whenever
they are shown), and the map is hard-capped at ``MAX_ROUTE_SERIES``. On
overflow the new key folds into ``(\"*\", \"(overflow)\")`` and a dropped
counter goes up: an unbounded label space is how a metrics registry becomes
the thing that OOMs the box, and ``_route_label``'s whole contract is that the
label space is the route table's, not the caller's.

Memory ceiling at cap: 2000 routes x (256 + 180) doubles x 8 bytes ≈ 7 MB.

Sync and lock-guarded like ``MetricsRegistry`` — the ASGI layer calls
``record()`` from the event loop, so nothing here may ``await``.
"""

from __future__ import annotations

import threading
from array import array

#: How many recent samples keep exact percentiles. Not a lifetime window.
RING = 256
#: Sparkline slots: 60 x 60s = 1 hour of per-minute aggregation.
SPARK_SLOTS = 60
#: Hard cap on distinct (method, route) keys.
MAX_ROUTE_SERIES = 2000

_OVERFLOW = ("*", "(overflow)")
_STATUS_CLASSES = ("2xx", "3xx", "4xx", "5xx")


class RouteSeries:
    """One endpoint's recent timings.

    ``err_count`` counts 5xx and raised handlers. A 4xx is a *client* error and
    does not increment it — the endpoint answered, the caller asked wrong. The
    status mix is kept separately so the difference is visible.
    """

    __slots__ = (
        "count",
        "err_count",
        "status_class",
        "ring",
        "_ring_i",
        "spark",
        "_spark_minute",
    )

    def __init__(self) -> None:
        self.count = 0
        self.err_count = 0
        self.status_class: dict[str, int] = {k: 0 for k in _STATUS_CLASSES}
        self.ring: array[float] = array("d", [0.0]) * RING
        self._ring_i = 0
        # per-minute: count / sum_ms / max_ms
        self.spark: tuple[array[float], array[float], array[float]] = (
            array("d", [0.0]) * SPARK_SLOTS,
            array("d", [0.0]) * SPARK_SLOTS,
            array("d", [0.0]) * SPARK_SLOTS,
        )
        self._spark_minute = -1

    def observe(self, duration_ms: float, status: int | None, minute: int) -> None:
        self.count += 1
        self.ring[self._ring_i] = duration_ms
        self._ring_i = (self._ring_i + 1) % RING

        if status is None or status >= 500:
            cls = "5xx"
            self.err_count += 1
        elif status >= 400:
            cls = "4xx"
        elif status >= 300:
            cls = "3xx"
        else:
            cls = "2xx"
        self.status_class[cls] += 1

        slot = minute % SPARK_SLOTS
        if minute != self._spark_minute:
            # A new minute reuses a slot; the previous occupant is the oldest.
            self.spark[0][slot] = 0.0
            self.spark[1][slot] = 0.0
            self.spark[2][slot] = 0.0
            self._spark_minute = minute
        self.spark[0][slot] += 1
        self.spark[1][slot] += duration_ms
        if duration_ms > self.spark[2][slot]:
            self.spark[2][slot] = duration_ms

    def percentiles(self) -> tuple[float | None, float | None, float | None]:
        """p50 / p95 / p99 over the ring's **recent window**, sorted exactly.

        Zero-filled ring slots are the unwritten tail (the ring is preallocated
        and circular), so the sample list is the slots written so far — counted
        by ``min(count, RING)``.
        """
        n = min(self.count, RING)
        if n <= 0:
            return None, None, None
        sample = sorted(self.ring[:n] if n < RING else self.ring)

        def q(p: float) -> float:
            if n == 1:
                return float(sample[0])
            idx = min(n - 1, max(0, int(round(p * (n - 1)))))
            return float(sample[idx])

        return q(0.5), q(0.95), q(0.99)


_series: dict[tuple[str, str], RouteSeries] = {}
_lock = threading.Lock()
_dropped = 0


def record(
    method: str, route: str, status: int | None, duration_ms: float, *, minute: int
) -> None:
    """Note one finished request against its endpoint.

    ``minute`` is ``int(time.time() // 60)`` — the caller owns the clock so the
    sparkline is testable without sleeping.
    """
    global _dropped
    key = (method.upper(), route)
    with _lock:
        s = _series.get(key)
        if s is None and len(_series) >= MAX_ROUTE_SERIES:
            _dropped += 1
            key = _OVERFLOW
            s = _series.get(key)
        if s is None:
            s = _series[key] = RouteSeries()
        s.observe(float(duration_ms), status, int(minute))


def snapshot() -> list[dict]:
    """One row per observed endpoint. Callers LEFT-JOIN the route table, so a
    never-hit endpoint is **absent here** rather than present as zeros —
    ``count: 0`` with ``p95: 0`` would read as "instant", and the truth is
    "no data".
    """
    with _lock:
        out: list[dict] = []
        for (method, route), s in _series.items():
            p50, p95, p99 = s.percentiles()
            counts, sums, maxes = s.spark
            spark: list[float | None] = []
            # 60 slots -> 24 points of per-minute average; empty slots stay None.
            step = SPARK_SLOTS // 24
            for i in range(0, SPARK_SLOTS, step):
                c = sum(counts[i : i + step])
                spark.append(round(sum(sums[i : i + step]) / c, 1) if c else None)
            out.append(
                {
                    "method": method,
                    "route": route,
                    "count": s.count,
                    "error_count": s.err_count,
                    "status": dict(s.status_class),
                    "p50": p50,
                    "p95": p95,
                    "p99": p99,
                    "spark": spark,
                }
            )
    return out


def dropped_series() -> int:
    with _lock:
        return _dropped


def reset() -> None:
    """Tests only: forget everything."""
    global _dropped
    with _lock:
        _series.clear()
        _dropped = 0
