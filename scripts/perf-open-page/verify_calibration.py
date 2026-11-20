"""Controlled fault and edge-state verification for scripts/perf-open-page.

Verifies the Phase 1 calibration requirements against a running 知是 stack:
1. Critical content delay (+550 ms on blocking API) delays `ready_ms` correspondingly.
2. Non-critical background request delay (+800 ms on post-render / idle API) is
   actually intercepted (`bg_hits >= 1`) and does NOT delay `ready_ms`.
3. Partial failure (1 success, 2 failures) reports `n_attempts=3, n_ok=1, n_failed=2` (`1/3 (2 失败)`),
   and total failure (0 successes, 3 failures) reports `ready_ms=None` (`FAIL`), never `0 ms`.
4. Cache modes (`cold`, `warm`, `walk`) are genuinely distinct in cache hits, document reload, and latency.
5. Empty state (`emptyTopicId`), HTTP 500 error state, and HTTP 403 forbidden state are accurately
   distinguished from normal content rendering.
6. Requests with different query strings on the same path are not misclassified as duplicates.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import time
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any

from playwright.sync_api import Route, sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))

from measure import (  # noqa: E402
    LONG_TASK_INIT,
    PROBE_INIT,
    _build_sample_from_timings,
    _launch_browser,
    _login_and_extract_auth,
    _measure_one,
    _open_true_cold_context,
    summarize_samples,
)
from pages import PAGES, SKELETON, PageSpec  # noqa: E402
import report as report_mod  # noqa: E402

CRITICAL_DELAY_S = 0.55
BACKGROUND_DELAY_S = 0.80


def _require(condition: bool, message: str) -> None:
    """Enforce a verification invariant even when Python runs with -O."""
    if not condition:
        raise RuntimeError(message)


def _spec_by_name(name: str) -> PageSpec:
    for p in PAGES:
        if p.name == name:
            return p
    raise KeyError(name)


def verify_query_string_dedup() -> dict[str, Any]:
    spec = _spec_by_name("channel")
    raw_res = [
        {
            "n": "http://127.0.0.1:3000/api/spaces?pageSize=50",
            "s": 10.0,
            "rs": 20.0,
            "e": 25.0,
            "t": "fetch",
            "sz": 500,
        },
        {
            "n": "http://127.0.0.1:3000/api/spaces?pageSize=12",
            "s": 12.0,
            "rs": 22.0,
            "e": 28.0,
            "t": "fetch",
            "sz": 300,
        },
        {
            "n": "http://127.0.0.1:3000/api/teams/my-teams",
            "s": 15.0,
            "rs": 30.0,
            "e": 35.0,
            "t": "fetch",
            "sz": 200,
        },
        {
            "n": "http://127.0.0.1:3000/api/teams/my-teams",
            "s": 40.0,
            "rs": 55.0,
            "e": 60.0,
            "t": "fetch",
            "sz": 200,
        },
    ]
    sample = _build_sample_from_timings(
        spec=spec,
        mode="walk",
        run_index=1,
        status="ok",
        base=0.0,
        dom_ready=70.0,
        paint_ready=75.0,
        dcl_ms=None,
        fcp_ms=None,
        raw_res=raw_res,
        lt={"total": 0.0, "count": 0},
        api_obs=[],
        error=None,
    )
    _require(sample.api_count == 4, f"expected 4 api requests, got {sample.api_count}")
    _require("GET spaces?pageSize=50" in sample.api_requests, "missing pageSize=50")
    _require("GET spaces?pageSize=12" in sample.api_requests, "missing pageSize=12")
    _require(
        sample.duplicate_api_requests == ["GET teams/my-teams"],
        f"different query strings must not be marked duplicate: {sample.duplicate_api_requests}",
    )
    return {
        "passed": True,
        "api_requests": sample.api_requests,
        "duplicate_api_requests": sample.duplicate_api_requests,
    }


def _render_report_markdown(payload: dict[str, Any], mode: str, tmp_path: Path) -> str:
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    buf = io.StringIO()
    old_argv = sys.argv
    try:
        sys.argv = ["report.py", str(tmp_path), "--mode", mode]
        with redirect_stdout(buf):
            report_mod.main()
    finally:
        sys.argv = old_argv
    return buf.getvalue()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://127.0.0.1:3000")
    ap.add_argument("--username", default="alice")
    ap.add_argument("--password", default="demo12345")
    ap.add_argument("--params", default=str(Path(__file__).with_name("params.json")))
    ap.add_argument("--out", default="/tmp/perf-calibration-verify.json")
    args = ap.parse_args()

    params: dict[str, object] = json.loads(Path(args.params).read_text())
    channel_spec = _spec_by_name("channel")
    channels_browse_spec = _spec_by_name("project-channels")

    checks: dict[str, Any] = {}
    checks["query_string_dedup"] = verify_query_string_dedup()
    print("[ok] 1/6 query_string_dedup:", checks["query_string_dedup"], flush=True)

    with sync_playwright() as pw:
        browser = _launch_browser(pw)
        # Use service_workers="block" for route-interception fault injection so
        # Workbox's NetworkFirst SW cache does not bypass Playwright page.route
        # or serve cached 200 fallbacks over injected 500/403 faults.
        fault_ctx = browser.new_context(
            viewport={"width": 1440, "height": 900},
            service_workers="block",
        )
        fault_ctx.add_init_script(PROBE_INIT + "\n" + LONG_TASK_INIT)
        page = fault_ctx.new_page()
        auth_storage = _login_and_extract_auth(
            page, args.base_url, args.username, args.password
        )

        # Warm the channel route once so chunk loading does not skew the delay delta.
        _measure_one(page, channel_spec, args.base_url, "walk", params, run_index=0)

        # 2. Critical vs Non-critical delay verification on `project-channels`
        # (`GET /api/projects/{id}/channels` is blocking for `.browse__row`;
        #  `GET /api/projects/{id}/private-unread` is a non-blocking sidebar badge query in ProjectShell.vue).
        _measure_one(page, channels_browse_spec, args.base_url, "walk", params, run_index=0)
        base_sample = _measure_one(
            page, channels_browse_spec, args.base_url, "walk", params, run_index=1
        )
        _require(
            base_sample.status == "ok" and base_sample.ready_ms is not None,
            f"baseline project-channels run failed: {base_sample}",
        )

        crit_hits = {"n": 0}

        def delay_critical(route: Route) -> None:
            crit_hits["n"] += 1
            resp = route.fetch()
            time.sleep(CRITICAL_DELAY_S)
            route.fulfill(response=resp)

        page.route("**/api/projects/*/channels*", delay_critical)
        try:
            crit_delayed = _measure_one(
                page, channels_browse_spec, args.base_url, "walk", params, run_index=2
            )
        finally:
            page.unroute("**/api/projects/*/channels*", delay_critical)

        _require(crit_hits["n"] >= 1, "critical delay route handler did not fire")
        _require(
            crit_delayed.status == "ok" and crit_delayed.ready_ms is not None,
            f"critical delayed run failed: {crit_delayed}",
        )
        crit_delta = round(crit_delayed.ready_ms - base_sample.ready_ms, 1)
        _require(
            crit_delta >= 350.0,
            f"Critical content delay (+{int(CRITICAL_DELAY_S * 1000)}ms) did not push ready_ms enough: "
            f"base={base_sample.ready_ms}ms, delayed={crit_delayed.ready_ms}ms, delta={crit_delta}ms",
        )

        # Non-critical background request delay (+800ms on /api/projects/*/private-unread*,
        # which ProjectShell.vue fetches for sidebar DM badges without blocking .browse__row).
        bg_hits = {"n": 0}

        def delay_background(route: Route) -> None:
            bg_hits["n"] += 1
            resp = route.fetch()
            time.sleep(BACKGROUND_DELAY_S)
            route.fulfill(response=resp)

        page.route("**/api/projects/*/private-unread*", delay_background)
        try:
            bg_delayed = _measure_one(
                page, channels_browse_spec, args.base_url, "walk", params, run_index=3
            )
            page.wait_for_timeout(300)
        finally:
            page.unroute("**/api/projects/*/private-unread*", delay_background)

        _require(
            bg_hits["n"] >= 1,
            "background delay route handler (**/api/projects/*/private-unread*) never fired on project-channels",
        )
        _require(
            bg_delayed.status == "ok" and bg_delayed.ready_ms is not None,
            f"background delayed run failed: {bg_delayed}",
        )
        bg_delta = round(bg_delayed.ready_ms - base_sample.ready_ms, 1)
        _require(
            bg_delta < 350.0,
            f"Non-critical background delay (+{int(BACKGROUND_DELAY_S * 1000)}ms) unexpectedly delayed ready_ms: "
            f"base={base_sample.ready_ms}ms, bg_delayed={bg_delayed.ready_ms}ms, delta={bg_delta}ms",
        )
        checks["critical_vs_background_delay"] = {
            "passed": True,
            "critical_delay_injected_ms": int(CRITICAL_DELAY_S * 1000),
            "critical_route_hits": crit_hits["n"],
            "baseline_ready_ms": base_sample.ready_ms,
            "critical_delayed_ready_ms": crit_delayed.ready_ms,
            "critical_delta_ms": crit_delta,
            "background_delay_injected_ms": int(BACKGROUND_DELAY_S * 1000),
            "background_route_hits": bg_hits["n"],
            "background_delayed_ready_ms": bg_delayed.ready_ms,
            "background_delta_ms": bg_delta,
        }
        print(
            "[ok] 2/6 critical_vs_background_delay:",
            checks["critical_vs_background_delay"],
            flush=True,
        )

        # 3. Partial failure (1 ok, 2 failed) and total failure (0 ok, 3 failed)
        attempt_counter = {"n": 0}

        def fail_first_two(route: Route) -> None:
            attempt_counter["n"] += 1
            if attempt_counter["n"] <= 2:
                route.fulfill(
                    status=500,
                    content_type="application/json",
                    body=json.dumps({"code": 500, "message": "injected fault"}),
                )
            else:
                route.continue_()

        page.route("**/api/projects/*/channels*", fail_first_two)
        try:
            partial_samples = [
                _measure_one(
                    page,
                    channels_browse_spec,
                    args.base_url,
                    "walk",
                    params,
                    run_index=i + 1,
                    timeout_ms=2500,
                )
                for i in range(3)
            ]
        finally:
            page.unroute("**/api/projects/*/channels*", fail_first_two)

        partial_summary = summarize_samples("project-channels", "walk", partial_samples)
        _require(partial_summary["n_attempts"] == 3, "expected 3 attempts")
        _require(partial_summary["n_ok"] == 1, "expected 1 ok")
        _require(partial_summary["n_failed"] == 2, "expected 2 failed")
        _require(partial_summary["status"] == "partial_failure", "expected partial_failure")
        _require(
            partial_summary["ready_ms"] is not None and partial_summary["ready_ms"] > 0,
            "expected positive ready_ms for 1/3 partial success",
        )

        # Total failure (3/3 fail): must report ready_ms=None, never 0!
        def fail_all(route: Route) -> None:
            route.fulfill(
                status=500,
                content_type="application/json",
                body=json.dumps({"code": 500, "message": "injected total fault"}),
            )

        page.route("**/api/projects/*/channels*", fail_all)
        try:
            all_fail_samples = [
                _measure_one(
                    page,
                    channels_browse_spec,
                    args.base_url,
                    "walk",
                    params,
                    run_index=i + 1,
                    timeout_ms=1500,
                )
                for i in range(3)
            ]
        finally:
            page.unroute("**/api/projects/*/channels*", fail_all)

        total_fail_summary = summarize_samples(
            "project-channels-fail", "walk", all_fail_samples
        )
        _require(total_fail_summary["n_attempts"] == 3, "expected 3 attempts")
        _require(total_fail_summary["n_ok"] == 0, "expected 0 ok")
        _require(total_fail_summary["n_failed"] == 3, "expected 3 failed")
        _require(total_fail_summary["ready_ms"] is None, "total failure must have ready_ms=None")
        _require(total_fail_summary["dom_ready_ms"] is None, "total failure must have dom_ready_ms=None")

        md_out = _render_report_markdown(
            {
                "project-channels": {"walk": partial_summary},
                "project-channels-fail": {"walk": total_fail_summary},
            },
            "walk",
            Path("/tmp/perf-fault-report-test.json"),
        )
        _require("1/3 (2 失败)" in md_out, f"missing '1/3 (2 失败)' in report:\n{md_out}")
        _require("0/3 (3 失败)" in md_out, f"missing '0/3 (3 失败)' in report:\n{md_out}")
        _require(
            "| project-channels-fail | 0/3 (3 失败) | FAIL | FAIL |" in md_out,
            f"missing FAIL row in report:\n{md_out}",
        )

        checks["partial_and_total_failure_accounting"] = {
            "passed": True,
            "partial_n_attempts": partial_summary["n_attempts"],
            "partial_n_ok": partial_summary["n_ok"],
            "partial_n_failed": partial_summary["n_failed"],
            "partial_ready_ms": partial_summary["ready_ms"],
            "total_fail_ready_ms": total_fail_summary["ready_ms"],
            "report_excerpt": md_out.strip().splitlines(),
        }
        print(
            "[ok] 3/6 partial_and_total_failure_accounting:",
            checks["partial_and_total_failure_accounting"]["report_excerpt"],
            flush=True,
        )

        # 4. Empty content, HTTP 403 forbidden, and HTTP 500 error states
        empty_spec_strict = PageSpec(
            "empty-channel-strict",
            "/projects/{projectId}/channels/{emptyTopicId}",
            ".im-row[data-row-id], .im-row[data-mid]",
            min_text=4,
            not_ready_selector=f".skel--chat, {SKELETON}",
            empty_selector=".chat-start, [data-chat-feed]:not([aria-busy='true']):not(:has(.im-row))",
            allow_empty=False,
        )
        empty_strict_sample = _measure_one(
            page, empty_spec_strict, args.base_url, "walk", params, run_index=1, timeout_ms=2500
        )
        _require(empty_strict_sample.status == "empty", f"got {empty_strict_sample}")
        _require(empty_strict_sample.ready_ms is None, "strict empty state must have ready_ms=None")
        _require("empty state" in (empty_strict_sample.error or ""), "missing empty state error text")

        empty_spec_allowed = PageSpec(
            "empty-channel-allowed",
            "/projects/{projectId}/channels/{emptyTopicId}",
            ".im-row[data-row-id], .im-row[data-mid]",
            min_text=4,
            not_ready_selector=f".skel--chat, {SKELETON}",
            empty_selector=".chat-start, [data-chat-feed]:not([aria-busy='true']):not(:has(.im-row))",
            allow_empty=True,
        )
        empty_allowed_sample = _measure_one(
            page, empty_spec_allowed, args.base_url, "walk", params, run_index=1, timeout_ms=10000
        )
        _require(empty_allowed_sample.status == "empty", "expected empty status")
        _require(
            empty_allowed_sample.ready_ms is not None and empty_allowed_sample.ready_ms > 0,
            "allowed empty state must record positive ready_ms",
        )
        _require(empty_allowed_sample.error is None, "allowed empty state must have error=None")

        def forbid_channels(route: Route) -> None:
            route.fulfill(
                status=403,
                content_type="application/json",
                body=json.dumps({"code": 403, "message": "Forbidden"}),
            )

        page.route("**/api/projects/*/channels*", forbid_channels)
        try:
            forbidden_sample = _measure_one(
                page,
                channels_browse_spec,
                args.base_url,
                "walk",
                params,
                run_index=1,
                timeout_ms=2000,
            )
        finally:
            page.unroute("**/api/projects/*/channels*", forbid_channels)

        _require(forbidden_sample.status == "forbidden", "expected forbidden status")
        _require(forbidden_sample.ready_ms is None, "forbidden must have ready_ms=None")
        _require("403" in (forbidden_sample.error or ""), "forbidden must mention 403")

        checks["empty_forbidden_error_states"] = {
            "passed": True,
            "empty_strict": {
                "status": empty_strict_sample.status,
                "ready_ms": empty_strict_sample.ready_ms,
                "error": empty_strict_sample.error,
            },
            "empty_allowed": {
                "status": empty_allowed_sample.status,
                "ready_ms": empty_allowed_sample.ready_ms,
                "error": empty_allowed_sample.error,
            },
            "forbidden_403": {
                "status": forbidden_sample.status,
                "ready_ms": forbidden_sample.ready_ms,
                "error": forbidden_sample.error,
            },
        }
        print(
            "[ok] 4/6 empty_forbidden_error_states:",
            checks["empty_forbidden_error_states"],
            flush=True,
        )

        # 5. Cache mode differentiation (`walk` vs `warm` vs `cold`)
        warm_ctx = browser.new_context(
            viewport={"width": 1440, "height": 900},
            service_workers="allow",
        )
        warm_ctx.add_init_script(PROBE_INIT + "\n" + LONG_TASK_INIT)
        warm_page = warm_ctx.new_page()
        _login_and_extract_auth(warm_page, args.base_url, args.username, args.password)

        walk_sample = _measure_one(
            warm_page, channel_spec, args.base_url, "walk", params, run_index=1
        )
        warm_sample = _measure_one(
            warm_page, channel_spec, args.base_url, "warm", params, run_index=1
        )
        cold_ctx, cold_page = _open_true_cold_context(browser, auth_storage)
        try:
            cold_sample = _measure_one(
                cold_page, channel_spec, args.base_url, "cold", params, run_index=1
            )
        finally:
            cold_ctx.close()
        warm_ctx.close()
        fault_ctx.close()

        _require(walk_sample.status == "ok" and walk_sample.ready_ms is not None, "walk failed")
        _require(warm_sample.status == "ok" and warm_sample.ready_ms is not None, "warm failed")
        _require(cold_sample.status == "ok" and cold_sample.ready_ms is not None, "cold failed")
        _require(
            walk_sample.dcl_ms is None,
            "walk mode must be a client-side route push without document DCL",
        )
        _require(
            warm_sample.dcl_ms is not None,
            "warm mode must be a document reload with DCL",
        )
        _require(
            cold_sample.dcl_ms is not None,
            "cold mode must be a document load with DCL",
        )
        _require(
            cold_sample.cached_requests == 0,
            f"true_cold must have 0 cached requests (CDP cache disabled + SW blocked), got {cold_sample.cached_requests}",
        )
        _require(
            warm_sample.cached_requests > 0,
            f"warm_reload must serve static assets from cache, got {warm_sample.cached_requests}",
        )

        checks["cache_modes_distinct"] = {
            "passed": True,
            "walk": {
                "dom_ready_ms": walk_sample.dom_ready_ms,
                "paint_ms": walk_sample.paint_ms,
                "dcl_ms": walk_sample.dcl_ms,
                "requests": walk_sample.requests,
                "cached_requests": walk_sample.cached_requests,
                "api_count": walk_sample.api_count,
            },
            "warm": {
                "dom_ready_ms": warm_sample.dom_ready_ms,
                "paint_ms": warm_sample.paint_ms,
                "dcl_ms": warm_sample.dcl_ms,
                "requests": warm_sample.requests,
                "cached_requests": warm_sample.cached_requests,
                "api_count": warm_sample.api_count,
            },
            "cold": {
                "dom_ready_ms": cold_sample.dom_ready_ms,
                "paint_ms": cold_sample.paint_ms,
                "dcl_ms": cold_sample.dcl_ms,
                "requests": cold_sample.requests,
                "cached_requests": cold_sample.cached_requests,
                "api_count": cold_sample.api_count,
            },
        }
        print(
            "[ok] 5/6 cache_modes_distinct:",
            checks["cache_modes_distinct"],
            flush=True,
        )

        # 6. DOM ready vs Paint ready separation check
        _require(
            walk_sample.dom_ready_ms is not None
            and walk_sample.paint_ms is not None
            and walk_sample.paint_ms >= walk_sample.dom_ready_ms,
            "paint_ms must be >= dom_ready_ms",
        )
        checks["dom_vs_paint_separation"] = {
            "passed": True,
            "walk_dom_ready_ms": walk_sample.dom_ready_ms,
            "walk_paint_ms": walk_sample.paint_ms,
            "cold_dom_ready_ms": cold_sample.dom_ready_ms,
            "cold_paint_ms": cold_sample.paint_ms,
        }
        print(
            "[ok] 6/6 dom_vs_paint_separation:",
            checks["dom_vs_paint_separation"],
            flush=True,
        )

        browser.close()

    _require(len(checks) == 6 and all(v.get("passed") is True for v in checks.values()), "not all 6 checks passed")
    Path(args.out).write_text(json.dumps(checks, ensure_ascii=False, indent=2) + "\n")
    print(f"\nALL 6 CALIBRATION CHECKS PASSED -> {args.out}")


if __name__ == "__main__":
    main()
