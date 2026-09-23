"""Per-endpoint timing store — ring, spark, cap, and the status rules."""

from app.core import route_metrics as rm


def setup_function() -> None:
    rm.reset()


def test_percentiles_are_exact_over_the_ring_window():
    for i in range(1, 257):
        rm.record("GET", "/a", 200, float(i), minute=0)
    rows = {r["route"]: r for r in rm.snapshot()}
    # Sorted samples are exactly 1..256. The index is round(p*(n-1)) clamped,
    # so p50 lands on sample[128] = 129, p95 on 243, p99 on 253.
    assert rows["/a"]["p50"] == 129.0
    assert rows["/a"]["p95"] == 243.0
    assert rows["/a"]["p99"] == 253.0
    assert rows["/a"]["count"] == 256


def test_ring_overwrites_the_oldest_sample():
    rm.record("GET", "/a", 200, 9999.0, minute=0)  # will be evicted
    for _ in range(rm.RING):
        rm.record("GET", "/a", 200, 1.0, minute=0)
    row = rm.snapshot()[0]
    assert row["count"] == rm.RING + 1
    # All remaining samples are 1.0 — the 9999 is gone.
    assert row["p99"] == 1.0


def test_status_mix_is_an_attribute_not_an_identity():
    rm.record("GET", "/a", 200, 1.0, minute=0)
    rm.record("GET", "/a", 404, 1.0, minute=0)
    rm.record("GET", "/a", 503, 1.0, minute=0)
    rm.record("GET", "/a", None, 1.0, minute=0)  # raised handler
    rows = rm.snapshot()
    assert len(rows) == 1  # one endpoint, one row
    row = rows[0]
    assert row["status"] == {"2xx": 1, "3xx": 0, "4xx": 1, "5xx": 2}
    # A 4xx is the caller's error; only 5xx/raised count as ours.
    assert row["error_count"] == 2
    assert row["count"] == 4


def test_spark_slot_rotates_per_minute():
    rm.record("GET", "/a", 200, 10.0, minute=0)
    rm.record("GET", "/a", 200, 30.0, minute=0)
    rm.record("GET", "/a", 200, 5.0, minute=1)
    # The per-minute slots hold the truth; the wire form downsamples 60 → 24.
    s = rm._series[("GET", "/a")]
    assert s.spark[0][0] == 2.0  # minute 0 saw two requests
    assert s.spark[1][0] == 40.0  # 10 + 30
    assert s.spark[0][1] == 1.0  # minute 1 saw one
    assert s.spark[1][1] == 5.0
    row = rm.snapshot()[0]
    assert any(v is not None for v in row["spark"])


def test_overflow_folds_and_counts_drops():
    for i in range(rm.MAX_ROUTE_SERIES):
        rm.record("GET", f"/r/{i}", 200, 1.0, minute=0)
    assert rm.dropped_series() == 0
    rm.record("GET", "/one-too-many", 200, 1.0, minute=0)
    rm.record("GET", "/and-another", 200, 1.0, minute=0)
    assert rm.dropped_series() == 2
    keys = {(r["method"], r["route"]) for r in rm.snapshot()}
    assert ("*", "(overflow)") in keys
    assert ("GET", "/one-too-many") not in keys


def test_never_hit_series_is_absent_not_zero():
    # The caller LEFT-JOINs the route table; inventing a zero row here would let
    # a never-hit endpoint print "0 ms", which reads as "instant".
    rm.record("GET", "/only", 200, 1.0, minute=0)
    routes = {r["route"] for r in rm.snapshot()}
    assert routes == {"/only"}


def test_empty_series_has_null_quantiles():
    rm.record("GET", "/a", 200, 1.0, minute=0)
    # Reach in and clear the written count so percentiles see nothing.
    s = rm._series[("GET", "/a")]
    s.count = 0
    assert s.percentiles() == (None, None, None)
