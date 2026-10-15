#!/usr/bin/env python3
"""Measure a frozen first-commit -> first-card cohort, without filling missing time.

Offline: python3 .claude/scripts/commit_card_phases.py cohort.json evidence.json

The cohort supplies the original endpoints, not a moving query of today's tasks.
Evidence contains bounded observations with a source ID; no observation means
unknown, never idle. Concurrent activities remain an explicit overlap bucket.
CI activity is not proof that CI blocked delivery. Tool envelopes are not CPU time.
This module does not modify lead_time_stages.py or its first-response calculation.
"""

from __future__ import annotations

import argparse
import itertools
import json
from collections import Counter
from datetime import datetime
from pathlib import Path

PHASES = ("local_check", "review", "edit", "ci", "ci_wait", "explicit_wait", "other_tool")
LABELS = {
    "local_check": "检查调用至返回记录",
    "review": "审查调用至返回记录",
    "edit": "修改调用至返回记录",
    "ci": "GitHub 检查运行",
    "ci_wait": "显式等待 CI 命令",
    "explicit_wait": "显式等待命令",
    "other_tool": "其他工具",
    "overlap": "多阶段重叠",
    "unknown": "拿不到阶段记录",
}


def timestamp(value: str) -> float:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamps must include a timezone")
    return parsed.timestamp()


def union_seconds(intervals: list[tuple[float, float]]) -> float:
    total = 0.0
    end = float("-inf")
    for a, b in sorted(intervals):
        if b < a:
            raise ValueError("negative interval")
        total += max(0.0, b - max(a, end))
        end = max(end, b)
    return total


def measure_line(line: dict, evidence: list[dict]) -> dict:
    lo, hi = timestamp(line["start"]), timestamp(line["end"])
    if hi < lo:
        raise ValueError(f"task {line['task']}: first card precedes first commit")
    intervals = {p: [] for p in PHASES}
    observed_ids = set()
    unbounded = []
    for event in evidence:
        if event["topic"] != line["topic"]:
            continue
        source = event.get("source")
        if not isinstance(source, str) or not source:
            raise ValueError("every observation needs a source ID")
        if source in observed_ids:
            raise ValueError(f"duplicate observation {source}")
        observed_ids.add(source)
        phase = event["phase"]
        if phase not in intervals:
            raise ValueError(f"unknown phase {phase}")
        start = event.get("start")
        end = event.get("end")
        if not start or not end:
            unbounded.append(source)
            continue
        a, b = timestamp(start), timestamp(end)
        if b < a:
            raise ValueError(f"negative observation {source}")
        a, b = max(lo, a), min(hi, b)
        if a < b:
            intervals[phase].append((a, b))

    # Sweep boundaries; a phase has multiplicity because jobs and tools overlap.
    boundaries = [(lo, None, 0), (hi, None, 0)]
    for phase, spans in intervals.items():
        for a, b in spans:
            boundaries.extend(((a, phase, 1), (b, phase, -1)))
    counts = Counter()
    partition = Counter({p: 0.0 for p in (*PHASES, "overlap", "unknown")})
    combinations = Counter()
    previous = lo
    for at, changes in itertools.groupby(sorted(boundaries, key=lambda x: x[0]), key=lambda x: x[0]):
        active = tuple(sorted(p for p, count in counts.items() if count > 0))
        bucket = "unknown" if not active else active[0] if len(active) == 1 else "overlap"
        partition[bucket] += at - previous
        if len(active) > 1:
            combinations[" + ".join(active)] += at - previous
        for _, phase, delta in changes:
            if phase:
                counts[phase] += delta
        previous = at
    return {
        **line,
        "wall_seconds": hi - lo,
        "exclusive_seconds": dict(partition),
        "inclusive_seconds": {p: union_seconds(spans) for p, spans in intervals.items()},
        "overlap_seconds": dict(combinations),
        "unbounded_sources": unbounded,
    }


def measure(cohort: dict, evidence: dict) -> dict:
    lines = cohort["lines"]
    if not lines:
        raise ValueError("cohort must not be empty")
    topics = [r["topic"] for r in lines]
    if len(set(topics)) != len(topics):
        raise ValueError("cohort must contain one row per task")
    extras = {r["topic"] for r in evidence["observations"]} - set(topics)
    if extras:
        raise ValueError("evidence outside the frozen cohort")
    measured = [measure_line(line, evidence["observations"]) for line in lines]
    totals = Counter()
    inclusive = Counter()
    overlaps = Counter()
    for row in measured:
        totals.update(row["exclusive_seconds"])
        inclusive.update(row["inclusive_seconds"])
        overlaps.update(row["overlap_seconds"])
    return {
        "cohort_provenance": cohort.get("provenance"),
        "evidence_provenance": evidence.get("provenance"),
        "lines": measured,
        "n": len(lines),
        "wall_seconds": sum(row["wall_seconds"] for row in measured),
        "exclusive_seconds": dict(totals),
        "inclusive_seconds": dict(inclusive),
        "overlap_seconds": dict(overlaps),
        "unbounded_observations": sum(len(row["unbounded_sources"]) for row in measured),
    }


def markdown(result: dict) -> str:
    wall = result["wall_seconds"]
    lines = [
        f"{result['n']} 条线，首提交至首次递卡合计 {wall / 60:.2f} 分钟。",
        "",
        "| 阶段 | 独占分钟 | 占总墙钟 | 含重叠分钟 |",
        "| --- | ---: | ---: | ---: |",
    ]
    for key, label in LABELS.items():
        value = result["exclusive_seconds"].get(key, 0)
        share = f"{100 * value / wall:.2f}%" if wall else "—"
        inclusive = result["inclusive_seconds"].get(key)
        text = "—" if inclusive is None else f"{inclusive / 60:.2f}"
        lines.append(f"| {label} | {value / 60:.2f} | {share} | {text} |")
    lines.extend([
        "",
        "独占列与多阶段重叠、未知相加等于总墙钟；含重叠列不能相加。",
        "CI 在运行不等于等待 CI；修改和审查只含有起止证据的工具时间，不含思考时间。",
        "没有完整记录的时间留在未知，不能解释成纯等待。",
        f"缺少起点或终点的观察：{result['unbounded_observations']} 条，未补时长。",
    ])
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cohort", type=Path)
    parser.add_argument("evidence", type=Path)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()
    result = measure(json.loads(args.cohort.read_text()), json.loads(args.evidence.read_text()))
    print(markdown(result), end="")
    if args.json:
        args.json.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
