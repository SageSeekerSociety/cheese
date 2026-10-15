#!/usr/bin/env python3
"""Record and summarize observed task-start phase timings; no network access.

Usage:
  python3 .claude/scripts/task_phase_marks.py <task-id> start <phase>
  python3 .claude/scripts/task_phase_marks.py <task-id> end <phase>
  python3 .claude/scripts/task_phase_marks.py report <task-id> [<task-id> ...]

Logs live under ~/.cache/cheese-task-phases, outside the repository. Start/end
must be recorded around the actual operation; this tool does not infer a phase
from the duration between unrelated events.
"""

import json
import sys
from contextlib import redirect_stdout
from io import StringIO
from tempfile import TemporaryDirectory
from datetime import UTC, datetime
from pathlib import Path

PHASES = ("worktree", "dependencies", "local_stack", "first_check", "coding")
LOG_DIR = Path.home() / ".cache" / "cheese-task-phases"


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def log_path(task: str) -> Path:
    if not task or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for c in task):
        raise ValueError("task id may contain only letters, digits, '-' and '_'")
    return LOG_DIR / f"{task}.jsonl"


def read_rows(path: Path, *, repair: bool = False) -> list[dict]:
    if not path.exists():
        return []
    raw = path.read_bytes()
    lines = raw.splitlines(keepends=True)
    rows, valid_bytes = [], 0
    for index, line in enumerate(lines):
        if not line.strip():
            valid_bytes += len(line)
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            if index == len(lines) - 1 and not line.endswith((b"\n", b"\r")):
                break
            raise
        valid_bytes += len(line)
    if repair and valid_bytes < len(raw):
        with path.open("r+b") as stream:
            stream.truncate(valid_bytes)
    if repair and valid_bytes and not raw[:valid_bytes].endswith(b"\n"):
        with path.open("ab") as stream:
            stream.write(b"\n")
    return rows


def record(task: str, action: str, phase: str) -> None:
    if phase not in PHASES:
        raise ValueError(f"unknown phase {phase!r}; choose: {', '.join(PHASES)}")
    path = log_path(task)
    rows = read_rows(path, repair=True)
    open_phases = set()
    for row in rows:
        if row["action"] == "start":
            open_phases.add(row["phase"])
        elif row["action"] == "end":
            open_phases.discard(row["phase"])
    if action == "start":
        if phase in open_phases:
            raise ValueError(f"{phase} already has an unmatched start marker")
    elif phase not in open_phases:
        raise ValueError(f"{phase} needs an unmatched start marker before end")
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"phase": phase, "action": action, "at": now()}) + "\n")
    print(path)


def measured_seconds_from_rows(rows: list[dict]) -> dict[str, float]:
    seconds: dict[str, float] = {}
    open_at: dict[str, datetime] = {}
    for row in rows:
        phase, action = row["phase"], row["action"]
        at = parse_time(row["at"])
        if action == "start":
            open_at[phase] = at
        elif action == "end" and phase in open_at:
            duration = (at - open_at.pop(phase)).total_seconds()
            if duration >= 0:
                seconds[phase] = seconds.get(phase, 0) + duration
    return seconds


def measured_seconds(task: str) -> dict[str, float]:
    return measured_seconds_from_rows(read_rows(log_path(task)))


def pct(values: list[float], fraction: float) -> float | None:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * fraction))] if ordered else None


def summarize(tasks: list[str]) -> None:
    results = {task: measured_seconds(task) for task in tasks}
    for task, seconds in results.items():
        total = sum(seconds.values())
        print(f"task={task} measured_phase_seconds={total:.3f}")
        for phase in PHASES:
            if phase not in seconds:
                print(f"  {phase}: not measured")
                continue
            share = 100 * seconds[phase] / total if total else 0
            print(f"  {phase}: {seconds[phase]:.3f}s ({share:.1f}% of measured phases)")
    if len(tasks) > 1:
        print("\nAcross tasks (nearest-rank percentiles):")
        for phase in PHASES:
            values = [row[phase] for row in results.values() if phase in row]
            if values:
                print(f"  {phase}: n={len(values)} p50={pct(values, .5):.3f}s "
                      f"p90={pct(values, .9):.3f}s")
            else:
                print(f"  {phase}: n=0")
    print("Shares are of explicitly marked phase time, not the full response-to-commit interval.")


def self_test() -> int:
    global LOG_DIR
    previous_log_dir = LOG_DIR
    with TemporaryDirectory() as directory:
        LOG_DIR = Path(directory)
        try:
            with redirect_stdout(StringIO()):
                record("self-test", "start", "coding")
                record("self-test", "end", "coding")
                record("self-test", "start", "coding")
                record("self-test", "end", "coding")
            path = log_path("self-test")
            with path.open("ab") as stream:
                stream.write(b'{"interrupted":')
            assert measured_seconds("self-test")["coding"] >= 0
            with redirect_stdout(StringIO()):
                record("self-test", "start", "first_check")
                record("self-test", "end", "first_check")
            assert "first_check" in measured_seconds("self-test")
        finally:
            LOG_DIR = previous_log_dir
    sample = [
        {"phase": "worktree", "action": "start", "at": "2026-10-14T10:00:00Z"},
        {"phase": "worktree", "action": "end", "at": "2026-10-14T10:01:11Z"},
    ]
    measured = measured_seconds_from_rows(sample)
    assert measured == {"worktree": 71}
    assert measured_seconds_from_rows(sample + [
        {"phase": "coding", "action": "start", "at": "2026-10-14T10:02:00Z"},
    ]) == {"worktree": 71}
    assert len(PHASES) == 5 and len(set(PHASES)) == len(PHASES)
    print("self-test: ok")
    return 0


def main(argv: list[str]) -> int:
    if argv == ["--self-test"]:
        return self_test()
    if len(argv) >= 2 and argv[0] == "report":
        summarize(argv[1:])
        return 0
    if len(argv) == 3 and argv[1] in {"start", "end"}:
        record(argv[0], argv[1], argv[2])
        return 0
    print(__doc__.strip(), file=sys.stderr)
    return 2


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"task_phase_marks: {exc}", file=sys.stderr)
        raise SystemExit(2)
