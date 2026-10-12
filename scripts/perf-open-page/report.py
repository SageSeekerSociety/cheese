"""Turn measure.py's JSON into the sorted table the live document carries.

    uv run python scripts/perf-open-page/report.py /tmp/perf-open-page.json --mode walk

Sorts by `ready_ms` (the time from the route switch to the page's own content
being on screen) and prints markdown. `--mode cold` prints the whole-page-load
numbers instead; both are the same table shape.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

COLS = [
    ("ready_ms", "可见"),
    ("last_response_ms", "最后一份响应"),
    ("network_ms", "网络跨度"),
    ("api_requests", "接口数"),
    ("api_bursts", "批次"),
    ("api_serial_ms", "串行段"),
    ("long_task_ms", "长任务"),
]


def cell(value) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.0f}"
    if isinstance(value, list):
        return str(len(value))
    return str(value)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--mode", default="walk")
    ap.add_argument("--json", action="store_true", help="print JSON instead of markdown")
    args = ap.parse_args()

    data = json.loads(Path(args.path).read_text())
    rows = []
    for page, per_mode in data.items():
        s = per_mode.get(args.mode)
        if not s:
            continue
        rows.append((s.get("ready_ms") or 0, page, s))
    rows.sort(reverse=True)

    if args.json:
        print(json.dumps([{**s, "page": p} for _, p, s in rows], ensure_ascii=False, indent=2))
        return

    header = ["页面"] + [label for _, label in COLS] + ["错误"]
    print("| " + " | ".join(header) + " |")
    print("|" + "|".join([" --- "] * len(header)) + "|")
    for _, page, s in rows:
        cells = [page] + [cell(s.get(key)) for key, _ in COLS]
        cells.append((s.get("error") or "")[:40])
        print("| " + " | ".join(cells) + " |")


if __name__ == "__main__":
    main()
