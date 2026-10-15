#!/usr/bin/env python3
"""Combine captured evidence; quarantine contradictory timestamps, never repair them.

Usage: commit_card_report.py cohort.json platform/evidence.json ci/evidence.json --out report-dir
Raw collector files remain unchanged. Reversed clock endpoints become unbounded
observations with both original values in anomalies.json, not zero-length spans.
"""

import argparse
import json
from pathlib import Path

from commit_card_phases import markdown, measure, timestamp


def combine(platform, ci):
    observations, anomalies = [], []
    for event in platform["observations"] + ci["observations"]:
        item = dict(event)
        if item.get("start") and item.get("end") and timestamp(item["end"]) < timestamp(item["start"]):
            anomalies.append({"topic": item["topic"], "source": item["source"],
                              "start": item["start"], "end": item["end"],
                              "reason": "completion precedes start; duration unavailable"})
            item["recorded_end"] = item["end"]
            item["end"] = None
            item["qualification"] = "contradictory-clock-endpoints"
        observations.append(item)
    return {"provenance": {"platform": platform["provenance"], "ci": ci["provenance"],
                           "normalization": "reversed endpoints quarantined as unbounded; raw files retained"},
            "observations": observations}, anomalies


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cohort", type=Path)
    parser.add_argument("platform", type=Path)
    parser.add_argument("ci", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    cohort, platform, ci = [json.loads(p.read_text()) for p in (args.cohort, args.platform, args.ci)]
    evidence, anomalies = combine(platform, ci)
    valid_lines, invalid_lines = [], []
    for line in cohort["lines"]:
        if timestamp(line["end"]) < timestamp(line["start"]):
            invalid_lines.append({"task": line["task"], "topic": line["topic"],
                                  "start": line["start"], "end": line["end"],
                                  "reason": "first card precedes first commit"})
        else:
            valid_lines.append(line)
    cohort_topics = {line["topic"] for line in cohort["lines"]}
    foreign_topics = sorted({event["topic"] for event in evidence["observations"]}
                            - cohort_topics)
    if foreign_topics:
        raise ValueError("evidence outside the frozen cohort: " + ", ".join(foreign_topics))
    valid_topics = {line["topic"] for line in valid_lines}
    evidence["observations"] = [event for event in evidence["observations"]
                                if event["topic"] in valid_topics]
    result = measure({"provenance": cohort.get("provenance"), "lines": valid_lines}, evidence)
    result["source_cohort_n"] = len(cohort["lines"])
    result["invalid_cohort_lines"] = invalid_lines
    args.out.mkdir(parents=True, exist_ok=True)
    for name, data in (("combined-evidence", evidence), ("anomalies", anomalies),
                       ("invalid-cohort-lines", invalid_lines), ("result", result)):
        (args.out / (name + ".json")).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    text = (markdown(result) +
            f"原始 {result['source_cohort_n']} 条线中有 {len(invalid_lines)} 条首卡早于首提交，未纳入阶段墙钟；\n"
            f"另有 {len(anomalies)} 条观测起止时刻倒置，按时长拿不到处理；原时刻见 anomalies.json。\n")
    (args.out / "table.md").write_text(text)
    print(text, end="")


if __name__ == "__main__":
    main()
