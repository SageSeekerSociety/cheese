#!/usr/bin/env python3
"""Measure observable pre-commit markers for recently started task lines.

Usage:
  OKCHEESE_USER=... OKCHEESE_PASS=... python3 .claude/scripts/task_phase_report.py \
      <channel-topic-id> <YYYY-MM-DD> <YYYY-MM-DD> [--json out.json]
  python3 .claude/scripts/task_phase_report.py --self-test

The report joins the existing lead-time rows with each task's read-only
transcript. It reports observed timestamps and computes only the cloud startup
span, whose explicit start and ready events are paired by sandbox id. Tool-event
timestamps are call times, not command completion times; missing boundaries stay missing.
"""

import argparse
import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from lead_time_stages import collect, stages  # noqa: E402
from orchestrator_sweep import get, login, ts  # noqa: E402


PHASE_ORDER = ("worktree", "dependencies", "local_stack", "first_check", "coding")
WORKTREE_RE = re.compile(r"(?:^|\s)cheese\s+worktree(?:\s|$)")
DEPENDENCY_RE = re.compile(
    r"(?:pnpm|npm|yarn|bun|uv|pip|poetry|cargo)\b[^\n]*(?:install|sync|setup|venv|lock)|"
    r"(?:install|sync)\s+(?:dependencies|deps)", re.I
)
LOCAL_STACK_RE = re.compile(
    r"(?:docker\s+compose|docker-compose)\b[^\n]*(?:\bup\b|\bstart\b)|"
    r"(?:docker|podman)\s+run\b|"
    r"(?:redis-server|valkey-server|postgres(?:ql)?|pg_ctl)\b|"
    r"dev-db\.sh\b[^\n]*\bstart\b|fake_redis_server\.py\b|"
    r"(?:task|just|make)\s+(?:dev[-:]?)?(?:stack|services|up)\b",
    re.I,
)
CHECK_RE = re.compile(
    r"(?:ci-fast|pytest|vitest|jest|ruff|mypy|pyright|tsc|lint|typecheck|test(?:s)?\b|check(?:s)?\b)",
    re.I,
)
EDIT_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit", "write_file", "edit"}


def iso(moment: datetime | None) -> str | None:
    return moment.isoformat().replace("+00:00", "Z") if moment else None


def page_transcript(token: str, topic: str, stop_before: str | None = None) -> list[dict]:
    """Read a task transcript back to its start, bounded to the measured line."""
    rows: list[dict] = []
    before = None
    for _ in range(200):
        path = f"/topics/{topic}/transcript?limit=200"
        if before:
            path += f"&before={before}"
        body = get(token, path)
        page = body.get("data") or []
        rows.extend(page)
        if not body.get("has_more") or not page:
            break
        stamps = [ts(row["created_at"]) for row in page if row.get("created_at")]
        if stop_before and stamps and min(stamps) <= ts(stop_before):
            cutoff = ts(stop_before)
            rows = [
                row for row in rows
                if row.get("created_at") and ts(row["created_at"]) >= cutoff
            ]
            break
        before = body.get("oldest_id") or page[0].get("id")
        if not before:
            break
    return sorted(rows, key=lambda row: (row.get("created_at") or "", row.get("id") or ""))


def text_for(row: dict) -> str:
    meta = row.get("meta") or {}
    return " ".join(str(meta.get(key) or "") for key in ("detail", "arg", "as_tool") ) + " " + str(row.get("content") or "")


def classify(row: dict) -> str | None:
    meta = row.get("meta") or {}
    tool = str(meta.get("tool") or "")
    if tool in EDIT_TOOLS:
        return "coding"
    if tool not in {"Bash", "bash"}:
        return None
    text = text_for(row)
    if WORKTREE_RE.search(text):
        return "worktree"
    if DEPENDENCY_RE.search(text):
        return "dependencies"
    if LOCAL_STACK_RE.search(text):
        return "local_stack"
    if CHECK_RE.search(text):
        return "first_check"
    return None


def transcript_markers(rows: list[dict]) -> dict:
    tools = []
    infra: dict[str, dict[str, list[datetime]]] = {}
    for row in rows:
        at_raw = row.get("created_at")
        if not at_raw:
            continue
        at = ts(at_raw)
        meta = row.get("meta") or {}
        event_type, home = meta.get("event_type"), meta.get("home")
        said = ((meta.get("i18n") or {}).get("content") or {}).get("key")
        if (
            event_type == "cloud_startup"
            and said not in {"sandboxReplaced", "cloudVmReplaced", "cloudVmReleasedIdle"}
            and home
        ):
            infra.setdefault(str(home), {"cloud_startup": [], "cloud_provisioning": []})[
                "cloud_startup"
            ].append(at)
        elif event_type == "cloud_provisioning" and home:
            infra.setdefault(str(home), {"cloud_startup": [], "cloud_provisioning": []})[
                "cloud_provisioning"
            ].append(at)
        if meta.get("tool"):
            tools.append((at, classify(row), str(meta.get("tool")), text_for(row)[:180]))
    startup = next(
        ((start, ready)
         for home in infra.values()
         for start in sorted(home["cloud_startup"])
         for ready in sorted(home["cloud_provisioning"])
         if ready >= start),
        (None, None),
    )
    result: dict = {
        "cloud_startup_at": iso(startup[0]),
        "cloud_ready_at": iso(startup[1]),
        "tool_count": len(tools),
    }
    for phase in PHASE_ORDER:
        hit = next((item for item in tools if item[1] == phase), None)
        if hit:
            result[f"{phase}_at"] = iso(hit[0])
            result[f"{phase}_tool"] = hit[2]
        else:
            result[f"{phase}_at"] = None
    result["first_action_at"] = iso(tools[0][0]) if tools else None
    result["first_action_tool"] = tools[0][2] if tools else None
    return result


def cloud_startup_minutes(markers: dict) -> float | None:
    """The platform records explicit preparation-start and ready events."""
    start, ready = markers.get("cloud_startup_at"), markers.get("cloud_ready_at")
    if not start or not ready:
        return None
    delta = (ts(ready) - ts(start)).total_seconds() / 60
    return delta if delta >= 0 else None


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    return sorted(values)[min(len(values) - 1, int(len(values) * fraction))]


def report(token: str, channel: str, start: str, end: str) -> dict:
    tasks, cards, ready, first_messages, gh = collect(token, channel, start, end)
    rows = stages(tasks, cards, ready, first_messages, gh, start, end)
    output = []
    task_by_number = {str(task.get("number")): task for task in tasks}
    for row in rows:
        if row.get("s2") is None or not row.get("conv"):
            continue
        task = task_by_number.get(str(row["task"])) or {}
        markers = transcript_markers(
            page_transcript(token, row["conv"], stop_before=task.get("created_at"))
        )
        first_response = first_messages.get(row["conv"])
        commit_at = ts(first_response) + timedelta(minutes=row["s2"]) if first_response else None
        row_out = {
            "task": row["task"],
            "title": row["title"],
            "started_at": task.get("started_at"),
            "first_response_at": iso(ts(first_response)) if first_response else None,
            "first_commit_at": iso(commit_at),
            "response_to_commit_minutes": row["s2"],
            "markers": markers,
            "cloud_startup_to_ready_minutes": cloud_startup_minutes(markers),
        }
        output.append(row_out)
    return {"channel": channel, "start": start, "end": end, "lines": output}


def render(data: dict) -> None:
    lines = data["lines"]
    print(f"task phase report: {len(lines)} lines with a first response and commit")
    observed = ("cloud_startup_at", "cloud_ready_at", "first_action_at",
                *[f"{phase}_at" for phase in PHASE_ORDER])
    for phase in observed:
        count = sum(1 for row in lines if row["markers"].get(phase))
        print(f"{phase}: observed in {count}/{len(lines)} lines")
    values = [row["cloud_startup_to_ready_minutes"] for row in lines
              if row["cloud_startup_to_ready_minutes"] is not None]
    if values:
        print(f"cloud startup → ready: n={len(values)} p50={percentile(values, .5):.1f}m "
              f"p90={percentile(values, .9):.1f}m max={max(values):.1f}m")
    else:
        print("cloud startup → ready: n=0")
    print("Tool-event timestamps are invocation times, not completion times.")
    print("Other phase durations remain unmeasured until explicit start/end markers exist.")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("channel", help="channel topic id")
    parser.add_argument("start", help="inclusive start date, YYYY-MM-DD")
    parser.add_argument("end", help="inclusive end date, YYYY-MM-DD")
    parser.add_argument("--json", dest="json_path", metavar="PATH")
    return parser.parse_args(argv)


def self_test() -> int:
    event = {"created_at": "2026-10-14T10:00:00Z", "meta": {"tool": "Bash", "detail": "cheese worktree t"}}
    assert classify(event) == "worktree"
    stack = {"created_at": "2026-10-14T10:00:30Z", "meta": {"tool": "Bash", "detail": "bash .claude/scripts/dev-db.sh start"}}
    assert classify(stack) == "local_stack"
    check = {"created_at": "2026-10-14T10:01:00Z", "meta": {"tool": "Bash", "detail": "python .github/scripts/ci-fast.py"}}
    assert classify(check) == "first_check"
    edit = {"created_at": "2026-10-14T10:02:00Z", "meta": {"tool": "Edit", "detail": "write test helper"}}
    assert classify(edit) == "coding"
    multi_edit = {"created_at": "2026-10-14T10:02:30Z", "meta": {"tool": "MultiEdit"}}
    assert classify(multi_edit) == "coding"
    rows = [
        {"created_at": "2026-10-14T10:00:00Z", "meta": {"event_type": "cloud_startup", "home": "h", "i18n": {"content": {"key": "cloudVmReleasedIdle"}}}},
        {"created_at": "2026-10-14T10:10:00Z", "meta": {"event_type": "cloud_startup", "home": "h", "i18n": {"content": {"key": "sandboxPreparing"}}}},
        {"created_at": "2026-10-14T10:20:00Z", "meta": {"event_type": "cloud_provisioning", "home": "h"}},
    ]
    markers = transcript_markers(rows)
    assert markers["cloud_startup_at"] == "2026-10-14T10:10:00Z"
    assert cloud_startup_minutes(markers) == 10
    assert percentile([1, 2, 3, 4, 5], .9) == 5
    args = parse_args(["--json", "out.json", "channel", "2026-10-14", "2026-10-14"])
    assert (args.channel, args.json_path) == ("channel", "out.json")
    print("self-test: ok")
    return 0


def main(argv: list[str]) -> int:
    if argv == ["--self-test"]:
        return self_test()
    args = parse_args(argv)
    token = login()
    data = report(token, args.channel, args.start, args.end)
    render(data)
    if args.json_path:
        with open(args.json_path, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
