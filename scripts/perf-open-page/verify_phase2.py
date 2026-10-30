#!/usr/bin/env python3
"""Interleaved Before/After A/B verification for Phase 2 optimizations.

Runs interleaved rounds (Before_1 -> After_1 -> Before_2 -> After_2 -> Before_3 -> After_3)
on the same VM, database, backend, and collab service by swapping `frontend/dist`
between the pre-built `origin/main` bundle (`/tmp/dist-before`) and the Phase 2
optimized bundle (`/tmp/dist-after`).

Preserves all raw samples and failures, records commit provenance and small-sample
uncertainty, and verifies guardrails (target content selector and key interactive
control readiness, failure preservation without zeroing, and eliminated redundant
critical-path requests).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import statistics
import subprocess
import sys
import time
from typing import Any

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent.parent
FRONTEND_DIST = REPO_ROOT / "frontend" / "dist"
DEFAULT_OUT = HERE / "phase2_ab_evidence.json"

TARGET_PAGES = [
    "project-members",
    "project-skills",
    "project-library",
]
MODES = ["walk", "warm", "cold"]


def _switch_dist(target_dir: Path) -> None:
    if not target_dir.is_dir():
        raise RuntimeError(f"Missing pre-built dist directory: {target_dir}")
    if FRONTEND_DIST.is_symlink() or FRONTEND_DIST.is_file():
        FRONTEND_DIST.unlink()
    elif FRONTEND_DIST.is_dir():
        shutil.rmtree(FRONTEND_DIST)
    FRONTEND_DIST.symlink_to(target_dir)


def _restore_dist_copy(target_dir: Path) -> None:
    if not target_dir.is_dir():
        return
    try:
        if FRONTEND_DIST.is_symlink() or FRONTEND_DIST.is_file():
            FRONTEND_DIST.unlink()
        elif FRONTEND_DIST.is_dir():
            shutil.rmtree(FRONTEND_DIST)
        shutil.copytree(target_dir, FRONTEND_DIST)
    except Exception as exc:  # noqa: BLE001
        print(f"warning: failed to restore {FRONTEND_DIST} from {target_dir}: {exc}", file=sys.stderr)


def _run_single_pass(
    base_url: str,
    variant: str,
    round_idx: int,
    out_path: Path,
    pages: list[str],
) -> dict[str, Any]:
    cmd = [
        sys.executable,
        str(HERE / "measure.py"),
        "--base-url",
        base_url,
        "--runs",
        "1",
        "--modes",
        ",".join(MODES),
        "--only",
        ",".join(pages),
        "--out",
        str(out_path),
    ]
    t0 = time.monotonic()
    subprocess.run(cmd, check=True)
    elapsed = round(time.monotonic() - t0, 2)
    data = json.loads(out_path.read_text())
    return {"variant": variant, "round": round_idx, "elapsed_s": elapsed, "data": data}


def _quantiles(vals: list[float]) -> dict[str, float | None]:
    if not vals:
        return {"p50": None, "p95": None, "min": None, "max": None, "spread": None}
    s = sorted(vals)
    p50 = round(statistics.median(s), 1)
    idx95 = min(len(s) - 1, int(round(0.95 * (len(s) - 1))))
    p95 = round(s[idx95], 1)
    return {
        "p50": p50,
        "p95": p95,
        "min": round(s[0], 1),
        "max": round(s[-1], 1),
        "spread": round(s[-1] - s[0], 1),
    }


def _count_matching_apis(api_timeline: list[list[Any]], pred) -> int:
    return sum(1 for item in api_timeline if pred(str(item[0])))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base-url", default="http://127.0.0.1:3000")
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--pages", default=",".join(TARGET_PAGES))
    ap.add_argument("--dist-before", default="/tmp/dist-before")
    ap.add_argument("--dist-after", default="/tmp/dist-after")
    ap.add_argument("--before-commit", default="490766388ece")
    ap.add_argument("--after-commit", default="HEAD")
    ap.add_argument("--reuse-tmp", action="store_true", help="Aggregate existing /tmp/perf-phase2-ab passes without re-running browser")
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    args = ap.parse_args()

    target_pages = [p.strip() for p in args.pages.split(",") if p.strip()]
    dist_before = Path(args.dist_before).resolve()
    dist_after = Path(args.dist_after).resolve()

    raw_passes: dict[str, list[dict[str, Any]]] = {"before": [], "after": []}
    tmp_dir = Path("/tmp/perf-phase2-ab")
    tmp_dir.mkdir(parents=True, exist_ok=True)

    if args.reuse_tmp:
        for r in range(1, args.rounds + 1):
            for variant in ("before", "after"):
                out_file = tmp_dir / f"pass_{variant}_r{r}.json"
                raw_passes[variant].append(
                    {"variant": variant, "round": r, "elapsed_s": 0.0, "data": json.loads(out_file.read_text())}
                )
    else:
        try:
            for r in range(1, args.rounds + 1):
                order = [("before", dist_before), ("after", dist_after)]
                if r % 2 == 0:
                    order.reverse()
                for variant, dist_path in order:
                    print(f"\n>>> Interleaved Round {r}/{args.rounds} [{variant.upper()}] <<<", flush=True)
                    _switch_dist(dist_path)
                    out_file = tmp_dir / f"pass_{variant}_r{r}.json"
                    res = _run_single_pass(args.base_url, variant, r, out_file, target_pages)
                    raw_passes[variant].append(res)
        finally:
            if dist_after.is_dir():
                _restore_dist_copy(dist_after)

    comparison: dict[str, Any] = {}
    guardrails_all_ok = True

    for page in target_pages:
        comparison[page] = {}
        for mode in MODES:
            var_summary: dict[str, Any] = {}
            for variant in ("before", "after"):
                samples: list[dict[str, Any]] = []
                for p in raw_passes[variant]:
                    rec = p["data"].get(page, {}).get(mode, {})
                    runs = rec.get("runs", [])
                    run0 = dict(runs[0]) if runs else {}
                    s = {
                        "interleaved_round": p["round"],
                        "status": rec.get("status", run0.get("status")),
                        "outcome": run0.get("outcome", rec.get("status")),
                        "dom_ready_ms": rec.get("dom_ready_ms", run0.get("dom_ready_ms")),
                        "paint_ms": rec.get("paint_ms", run0.get("paint_ms")),
                        "critical_path_api_ms": rec.get("critical_path_api_ms", run0.get("critical_path_api_ms")),
                        "api_count": rec.get("api_count", run0.get("api_count")),
                        "long_task_ms": rec.get("long_task_ms", run0.get("long_task_ms", 0.0)),
                        "error": rec.get("error", run0.get("error")),
                        "api_timeline": rec.get("api_timeline", run0.get("api_timeline", [])),
                    }
                    samples.append(s)
                ok_samples = [s for s in samples if s.get("status") == "ok" and s.get("paint_ms") is not None]
                dom_vals = [float(s["dom_ready_ms"]) for s in ok_samples if s.get("dom_ready_ms") is not None]
                paint_vals = [float(s["paint_ms"]) for s in ok_samples if s.get("paint_ms") is not None]
                crit_vals = [float(s["critical_path_api_ms"]) for s in ok_samples if s.get("critical_path_api_ms") is not None]
                api_counts = [int(s["api_count"]) for s in ok_samples if s.get("api_count") is not None]
                lt_vals = [float(s.get("long_task_ms", 0.0)) for s in ok_samples]

                # Count specific redundant calls across all ok samples (median)
                unbatched_room_list = [
                    _count_matching_apis(
                        s.get("api_timeline", []),
                        lambda u: any(
                            p in u
                            for p in (
                                "/threads?limit=100",
                                "/tasks?limit=0&status=open",
                                "/tasks?limit=0&status=closed",
                                "/pins",
                                "/chapters",
                            )
                        ),
                    )
                    for s in ok_samples
                ]
                hidden_settings_list = [
                    _count_matching_apis(
                        s.get("api_timeline", []),
                        lambda u: any(
                            u.endswith(p)
                            for p in (
                                "/upstream",
                                "/forge",
                                "/commit-credit",
                                "/branch-protection",
                                "/roles",
                                "/credit-budget",
                            )
                        ),
                    )
                    for s in ok_samples
                ]
                default_model_list = [
                    _count_matching_apis(s.get("api_timeline", []), lambda u: u.endswith("/default-model"))
                    for s in ok_samples
                ]
                my_teams_list = [
                    _count_matching_apis(s.get("api_timeline", []), lambda u: u == "teams/my-teams")
                    for s in ok_samples
                ]
                projects_list = [
                    _count_matching_apis(s.get("api_timeline", []), lambda u: u == "projects")
                    for s in ok_samples
                ]
                address_project_list = [
                    _count_matching_apis(s.get("api_timeline", []), lambda u: u.startswith("addresses/projects/"))
                    for s in ok_samples
                ]

                unbatched_room_calls = int(statistics.median(unbatched_room_list)) if unbatched_room_list else 0
                hidden_settings_calls = int(statistics.median(hidden_settings_list)) if hidden_settings_list else 0
                default_model_calls = int(statistics.median(default_model_list)) if default_model_list else 0
                my_teams_calls = int(statistics.median(my_teams_list)) if my_teams_list else 0
                projects_calls = int(statistics.median(projects_list)) if projects_list else 0
                address_project_calls = int(statistics.median(address_project_list)) if address_project_list else 0

                var_summary[variant] = {
                    "n_attempts": len(samples),
                    "n_ok": len(ok_samples),
                    "n_failed": len(samples) - len(ok_samples),
                    "small_sample": len(ok_samples) < 5,
                    "dom_ready_ms": _quantiles(dom_vals),
                    "paint_ms": _quantiles(paint_vals),
                    "critical_path_api_ms": _quantiles(crit_vals),
                    "api_count_p50": round(statistics.median(api_counts), 1) if api_counts else None,
                    "long_task_ms_p50": round(statistics.median(lt_vals), 1) if lt_vals else None,
                    "unbatched_room_calls": unbatched_room_calls,
                    "hidden_settings_calls": hidden_settings_calls,
                    "default_model_calls": default_model_calls,
                    "my_teams_calls": my_teams_calls,
                    "projects_calls": projects_calls,
                    "address_project_calls": address_project_calls,
                    "raw_samples": [
                        {
                            "round": s.get("interleaved_round"),
                            "outcome": s.get("outcome"),
                            "dom_ready_ms": s.get("dom_ready_ms"),
                            "paint_ms": s.get("paint_ms"),
                            "critical_path_api_ms": s.get("critical_path_api_ms"),
                            "api_count": s.get("api_count"),
                            "long_task_ms": s.get("long_task_ms"),
                            "error": s.get("error"),
                        }
                        for s in samples
                    ],
                }

            b = var_summary["before"]
            a = var_summary["after"]
            b_paint = b["paint_ms"]["p50"]
            a_paint = a["paint_ms"]["p50"]
            b_dom = b["dom_ready_ms"]["p50"]
            a_dom = a["dom_ready_ms"]["p50"]
            b_crit = b["critical_path_api_ms"]["p50"]
            a_crit = a["critical_path_api_ms"]["p50"]

            delta_paint_ms = round(a_paint - b_paint, 1) if (b_paint is not None and a_paint is not None) else None
            delta_paint_pct = round(100.0 * (a_paint - b_paint) / b_paint, 1) if (b_paint and a_paint is not None) else None
            delta_dom_ms = round(a_dom - b_dom, 1) if (b_dom is not None and a_dom is not None) else None
            delta_crit_ms = round(a_crit - b_crit, 1) if (b_crit is not None and a_crit is not None) else None
            delta_api_count = (
                round(a["api_count_p50"] - b["api_count_p50"], 1)
                if (b["api_count_p50"] is not None and a["api_count_p50"] is not None)
                else None
            )

            # Check guardrails (preserve and report any transient warmup error in raw_samples)
            guardrail_checks: dict[str, bool] = {
                "selector_and_action_control_verified": a["n_ok"] >= 1,
                "failures_preserved_not_zeroed": all(
                    (s["paint_ms"] is None) for s in a["raw_samples"] if s["outcome"] != "ready" and s["outcome"] != "ok"
                ),
            }
            if page == "thread":
                guardrail_checks["eliminated_unbatched_room_piece_fetches"] = (
                    b["unbatched_room_calls"] >= 4 and a["unbatched_room_calls"] == 0
                )
            elif page == "project-settings":
                guardrail_checks["eliminated_hidden_section_fetches"] = (
                    b["hidden_settings_calls"] >= 5 and a["hidden_settings_calls"] == 0
                )
                guardrail_checks["eliminated_duplicate_default_model_fetch"] = (
                    b["default_model_calls"] >= 2 and a["default_model_calls"] == 1
                )
            elif page == "teams":
                guardrail_checks["eliminated_duplicate_my_teams_fetch"] = (
                    b["my_teams_calls"] >= 2 and a["my_teams_calls"] <= 1
                )
            elif page == "settings-connections" and mode == "warm":
                guardrail_checks["eliminated_duplicate_projects_fetch"] = (
                    b["projects_calls"] >= 2 and a["projects_calls"] <= 1
                )
            elif page in ("project-library", "project-members", "project-skills") and mode == "cold":
                guardrail_checks["eliminated_cold_address_project_lookup"] = (
                    a["address_project_calls"] == 0
                )

            if not all(guardrail_checks.values()):
                guardrails_all_ok = False

            noise_spread_ms = max(b["paint_ms"]["spread"] or 0.0, a["paint_ms"]["spread"] or 0.0)
            eliminated_guardrail = any(k.startswith("eliminated_") and v for k, v in guardrail_checks.items())
            if delta_paint_ms is not None and delta_paint_ms < 0 and (b["paint_ms"]["min"] or 0) > (a["paint_ms"]["max"] or 0):
                claim_status = "confirmed_non_overlapping_improvement"
            elif delta_crit_ms is not None and delta_crit_ms < -50:
                claim_status = "confirmed_critical_path_api_improvement"
            elif eliminated_guardrail and (delta_paint_ms is None or delta_paint_ms >= -noise_spread_ms):
                claim_status = "withdrawn_latency_claim_within_noise_band_redundant_request_eliminated"
            else:
                claim_status = "within_noise_band"

            comparison[page][mode] = {
                "before": b,
                "after": a,
                "delta": {
                    "dom_ready_ms": delta_dom_ms,
                    "paint_ms": delta_paint_ms,
                    "paint_pct": delta_paint_pct,
                    "critical_path_api_ms": delta_crit_ms,
                    "api_count": delta_api_count,
                    "noise_spread_ms": noise_spread_ms,
                    "claim_status": claim_status,
                },
                "guardrails": guardrail_checks,
            }

    out_payload = {
        "_meta": {
            "protocol": "interleaved_ab_alternating_order",
            "rounds": args.rounds,
            "small_sample": args.rounds < 5,
            "before_commit": args.before_commit,
            "after_commit": args.after_commit,
            "environment": {
                "base_url": args.base_url,
                "viewport": "1440x900",
                "backend_port": 8081,
                "collab_port": 8902,
                "postgres_port": 5433,
                "redis_port": 6379,
            },
            "target_pages": target_pages,
            "modes": MODES,
            "guardrails_all_ok": guardrails_all_ok,
            "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        },
        "comparison": comparison,
    }
    Path(args.out).write_text(json.dumps(out_payload, ensure_ascii=False, indent=2) + "\n")
    print(f"\n=== Interleaved A/B Summary (N={args.rounds}) ===")
    for page in target_pages:
        for mode in MODES:
            c = comparison[page][mode]
            b_p = c["before"]["paint_ms"]
            a_p = c["after"]["paint_ms"]
            d = c["delta"]
            print(
                f"{page:22s} {mode:5s} | "
                f"before={b_p['p50']}ms [{b_p['min']}..{b_p['max']}] (api={c['before']['api_count_p50']}, crit={c['before']['critical_path_api_ms']['p50']}) -> "
                f"after={a_p['p50']}ms [{a_p['min']}..{a_p['max']}] (api={c['after']['api_count_p50']}, crit={c['after']['critical_path_api_ms']['p50']}) | "
                f"delta_paint={d['paint_ms']}ms ({d['paint_pct']}%) delta_crit={d['critical_path_api_ms']}ms delta_api={d['api_count']} | "
                f"guardrails={c['guardrails']}"
            )
    print(f"written: {args.out} (guardrails_all_ok={guardrails_all_ok})")


if __name__ == "__main__":
    main()
