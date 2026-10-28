"""Turn measure.py's JSON into a calibrated Markdown report or JSON summary.

    uv run python scripts/perf-open-page/report.py /tmp/perf-open-page.json --mode walk

Key rules enforced by this report:
* Never turns a total failure (`ready_ms is None` or `n_ok == 0`) into `0 ms`;
  failed pages sort to the top of the attention list with `FAIL (0/N)`.
* Explicitly prints `成功/尝试` (e.g. `1/3 (2 失败)`) when partial failures occur.
* Separates `DOM就绪` (`dom_ready_ms`) and `真实绘制` (`paint_ms` / `ready_ms`).
* Shows `[min–max]` range when `n_ok < 5` instead of claiming a noisy p95, and
  prints `p95` when `n_ok >= 5`.
* Uses `api_count` (preserving query strings and repeated requests) and lists
  true duplicate API calls (`duplicate_api_requests`) separately.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

MODE_ALIASES = {
    "walk": "walk",
    "spa_walk": "walk",
    "warm": "warm",
    "warm_reload": "warm",
    "cold": "cold",
    "true_cold": "cold",
}


def _fmt_ms(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, (int, float)):
        if math.isnan(value):
            return "—"
        return f"{value:.0f}"
    return str(value)


def _fmt_attempts(s: dict[str, Any]) -> str:
    n_att = s.get("n_attempts")
    n_ok = s.get("n_ok")
    n_fail = s.get("n_failed")
    if n_att is None:
        return "— (legacy)" if not s.get("error") else "0/1 (1 失败)"
    if n_fail:
        return f"{n_ok}/{n_att} ({n_fail} 失败)"
    return f"{n_ok}/{n_att}"


def _fmt_spread(s: dict[str, Any]) -> str:
    stats = s.get("stats")
    if not stats:
        return "—"
    n = stats.get("n", 0)
    lo, hi = stats.get("min_ms"), stats.get("max_ms")
    if n < 5:
        return f"[{lo:.0f}–{hi:.0f}] (N={n})"
    return f"p95={stats.get('p95_ms', hi):.0f} [{lo:.0f}–{hi:.0f}]"


def _fmt_api(s: dict[str, Any]) -> str:
    count = s.get("api_count")
    if count is None:
        reqs = s.get("api_requests")
        count = len(reqs) if isinstance(reqs, list) else 0
    dups = s.get("duplicate_api_requests") or []
    return f"{count} ({len(dups)} 重复)" if dups else str(count)


def _sort_key(item: tuple[str, dict[str, Any]]) -> tuple[int, float, str]:
    page, s = item
    ready = s.get("ready_ms")
    n_ok = s.get("n_ok", 0 if (ready is None or (isinstance(ready, float) and math.isnan(ready))) else 1)
    # Sort complete failures first (priority 1, inf) so they are never hidden at 0 ms.
    if n_ok == 0 or ready is None or (isinstance(ready, float) and math.isnan(ready)):
        return (1, float("inf"), page)
    return (0, float(ready), page)


def _print_meta(meta: dict[str, Any] | None, mode: str) -> None:
    if not meta:
        return
    tw = meta.get("time_window") or {}
    pol = (meta.get("cache_policies") or {}).get(mode) or {}
    dev = meta.get("device") or {}
    print(
        f"- 构建版本: `{meta.get('build_version', 'unknown')}` | "
        f"运行版本: `{meta.get('running_version', 'unknown')}` | "
        f"浏览器: `{meta.get('browser', 'unknown')}`"
    )
    print(
        f"- 模式: `{mode}` (`{pol.get('canonical_name', mode)}`) | "
        f"HTTP 缓存: `{pol.get('http_cache', 'unknown')}` | "
        f"Service Worker: `{pol.get('service_worker', 'unknown')}` | "
        f"每页尝试: `{meta.get('runs_per_mode', 'unknown')}`"
    )
    print(
        f"- 设备/视口: `CPU={dev.get('cpu_count', 'unknown')}, {dev.get('viewport', {})}` | "
        f"时间窗口: `{tw.get('started_at', 'unknown')}` → `{tw.get('finished_at', 'unknown')}` | "
        f"分位数算法: `{meta.get('quantile_method', 'unknown')}`"
    )
    print()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--mode", default="walk")
    ap.add_argument("--json", action="store_true", help="print JSON instead of markdown")
    args = ap.parse_args()

    mode = MODE_ALIASES.get(args.mode, args.mode)
    data = json.loads(Path(args.path).read_text())
    meta = data.get("_meta") if isinstance(data, dict) else None

    items: list[tuple[str, dict[str, Any]]] = []
    for page, per_mode in data.items():
        if page == "_meta" or not isinstance(per_mode, dict):
            continue
        s = per_mode.get(mode) or per_mode.get(args.mode)
        if not s:
            continue
        items.append((page, s))

    items.sort(key=_sort_key, reverse=True)

    if args.json:
        print(
            json.dumps(
                {"_meta": meta, "mode": mode, "rows": [{**s, "page": p} for p, s in items]},
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    _print_meta(meta, mode)

    header = [
        "页面/操作",
        "成功/尝试",
        "DOM就绪(ms)",
        "真实绘制(ms)",
        "范围 / p95",
        "关键路径API(ms)",
        "最后响应(ms)",
        "接口数(重复)",
        "长任务(ms)",
        "状态/错误",
    ]
    print("| " + " | ".join(header) + " |")
    print("|" + "|".join([" --- "] * len(header)) + "|")
    for page, s in items:
        ready = s.get("ready_ms")
        n_ok = s.get("n_ok", 0 if ready is None else 1)
        paint_cell = "FAIL" if (n_ok == 0 or ready is None) else _fmt_ms(ready)
        dom_cell = "FAIL" if (n_ok == 0 or ready is None) else _fmt_ms(s.get("dom_ready_ms", ready))
        crit_api = s.get("critical_path_api_ms", s.get("network_ms"))
        err_text = (s.get("error") or s.get("status") or "ok")[:48]
        cells = [
            page,
            _fmt_attempts(s),
            dom_cell,
            paint_cell,
            _fmt_spread(s),
            _fmt_ms(crit_api),
            _fmt_ms(s.get("last_response_ms")),
            _fmt_api(s),
            _fmt_ms(s.get("long_task_ms")),
            err_text,
        ]
        print("| " + " | ".join(cells) + " |")


if __name__ == "__main__":
    main()
