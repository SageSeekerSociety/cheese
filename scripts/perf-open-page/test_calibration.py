"""Unit tests for the structural invariants of scripts/perf-open-page and scripts/perf-awaiting-me.

Guards deterministic rules (no timing thresholds):
* Database guards refuse non-loopback hosts and business database names.
* Partial failures report exact attempt/ok/fail counts and preserve all raw runs.
* Total failures (`n_ok == 0`) report `ready_ms = None` (`FAIL`), never `0 ms`.
* Query strings are preserved in `api_requests`, and only identical `METHOD path?query`
  calls are flagged in `duplicate_api_requests`.
* Parallel blocking requests are merged by interval union (`critical_path_api_ms`),
  never summed, and post-DOM-ready requests land in `background_api_timeline`.
"""

from __future__ import annotations

import io
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "perf-open-page"))
sys.path.insert(0, str(ROOT / "scripts" / "perf-awaiting-me"))


def test_perf_db_guard_refuses_business_and_remote_databases() -> None:
    import seed_awaiting_me

    guard = getattr(seed_awaiting_me, "assert_safe_perf_dsn", None)
    assert guard is not None, "seed_awaiting_me must define assert_safe_perf_dsn"
    assert guard("postgresql://cheesex:cheesex@127.0.0.1:5433/cheese_perf") == "cheese_perf"
    assert guard("postgresql://cheesex:cheesex@localhost:5433/my_test_db") == "my_test_db"

    for unsafe in (
        "postgresql://cheesex:cheesex@127.0.0.1:5433/cheesex",
        "postgresql://cheesex:cheesex@127.0.0.1:5433/postgres",
        "postgresql://cheesex:cheesex@127.0.0.1:5433/production",
        "postgresql://cheesex:cheesex@db.internal:5432/cheese_perf",
        "postgresql://cheesex:cheesex@10.0.0.5:5432/cheese_test",
    ):
        with pytest.raises(RuntimeError):
            guard(unsafe)


def test_partial_and_total_failure_never_report_zero_ms(tmp_path: Path) -> None:
    import measure
    import report as report_mod

    summarize = getattr(measure, "summarize_samples", None)
    assert summarize is not None, "measure must define summarize_samples preserving raw runs"
    Sample = measure.Sample

    partial = summarize(
        "channel",
        "walk",
        [
            Sample(page="channel", mode="walk", run_index=1, status="timeout", error="Timeout 30000ms"),
            Sample(page="channel", mode="walk", run_index=2, status="error", error="HTTP 500"),
            Sample(
                page="channel",
                mode="walk",
                run_index=3,
                status="ok",
                dom_ready_ms=310.0,
                paint_ms=325.0,
                ready_ms=325.0,
                requests=12,
                api_count=4,
            ),
        ],
    )
    assert partial["n_attempts"] == 3
    assert partial["n_ok"] == 1
    assert partial["n_failed"] == 2
    assert partial["status"] == "partial_failure"
    assert partial["ready_ms"] == 325.0
    assert partial["dom_ready_ms"] == 310.0
    assert len(partial["runs"]) == 3
    assert partial["failure_breakdown"] == {"timeout": 1, "error": 1}

    total_fail = summarize(
        "broken-page",
        "walk",
        [
            Sample(page="broken-page", mode="walk", run_index=1, status="timeout", error="Timeout"),
            Sample(page="broken-page", mode="walk", run_index=2, status="forbidden", error="HTTP 403"),
            Sample(page="broken-page", mode="walk", run_index=3, status="cancelled", error="Target closed"),
        ],
    )
    assert total_fail["n_attempts"] == 3
    assert total_fail["n_ok"] == 0
    assert total_fail["n_failed"] == 3
    assert total_fail["ready_ms"] is None
    assert total_fail["dom_ready_ms"] is None
    assert total_fail["paint_ms"] is None
    assert total_fail["stats"] is None
    assert len(total_fail["runs"]) == 3

    out_file = tmp_path / "report.json"
    out_file.write_text(
        json.dumps(
            {
                "channel": {"walk": partial},
                "broken-page": {"walk": total_fail},
            }
        )
    )
    buf = io.StringIO()
    old_argv = sys.argv
    try:
        sys.argv = ["report.py", str(out_file), "--mode", "walk"]
        with redirect_stdout(buf):
            report_mod.main()
    finally:
        sys.argv = old_argv

    markdown = buf.getvalue()
    assert "| broken-page | 0/3 (3 失败) | FAIL | FAIL |" in markdown
    assert "| channel | 1/3 (2 失败) | 310 | 325 | [325–325] (N=1) |" in markdown


def test_query_string_preserved_and_parallel_or_background_not_summed() -> None:
    import measure
    from pages import PAGES

    build_fn = getattr(measure, "_build_sample_from_timings", None)
    assert build_fn is not None, "measure must define _build_sample_from_timings"
    spec = next(p for p in PAGES if p.name == "channel")
    raw_res = [
        {
            "n": "http://127.0.0.1:3000/api/spaces?pageSize=50",
            "s": 10.0,
            "rs": 40.0,
            "e": 60.0,
            "t": "fetch",
            "sz": 500,
        },
        {
            "n": "http://127.0.0.1:3000/api/spaces?pageSize=12",
            "s": 20.0,
            "rs": 50.0,
            "e": 80.0,
            "t": "fetch",
            "sz": 300,
        },
        {
            "n": "http://127.0.0.1:3000/api/topics/123/environment",
            "s": 120.0,
            "rs": 180.0,
            "e": 200.0,
            "t": "fetch",
            "sz": 100,
        },
    ]
    sample = build_fn(
        spec=spec,
        mode="walk",
        run_index=1,
        status="ok",
        base=0.0,
        dom_ready=100.0,
        paint_ready=112.0,
        dcl_ms=None,
        fcp_ms=None,
        raw_res=raw_res,
        lt={"total": 55.0, "count": 1},
        api_obs=[
            {
                "method": "GET",
                "path": "spaces?pageSize=50",
                "status": 200,
                "request_id": "req-1",
                "server_timing_ms": 22.5,
            },
            {
                "method": "GET",
                "path": "spaces?pageSize=12",
                "status": 200,
                "request_id": "req-2",
                "server_timing_ms": 18.0,
            },
            {
                "method": "GET",
                "path": "topics/123/environment",
                "status": 200,
                "request_id": "req-3",
                "server_timing_ms": 40.0,
            },
        ],
        error=None,
    )
    assert sample.api_count == 3
    assert "GET spaces?pageSize=50" in sample.api_requests
    assert "GET spaces?pageSize=12" in sample.api_requests
    assert sample.duplicate_api_requests == []
    assert sample.critical_path_api_ms == 70.0
    assert len(sample.blocking_api_timeline) == 2
    assert len(sample.background_api_timeline) == 1
    assert sample.background_api_timeline[0]["path"] == "topics/123/environment"
    assert sample.blocking_api_timeline[0]["request_id"] == "req-1"
    assert sample.blocking_api_timeline[0]["server_timing_app_ms"] == 22.5


def test_cache_policies_declared_distinctly() -> None:
    import measure

    policies = getattr(measure, "CACHE_POLICIES", None)
    assert policies is not None, "measure must declare CACHE_POLICIES"
    assert policies["cold"]["service_worker"] == "block"
    assert policies["cold"]["http_cache"] == "disabled_via_cdp"
    assert policies["warm"]["service_worker"] == "allow"
    assert policies["warm"]["http_cache"] == "enabled"
    assert policies["walk"]["service_worker"] == "allow"
    assert policies["walk"]["http_cache"] == "enabled"
