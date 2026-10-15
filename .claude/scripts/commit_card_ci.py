#!/usr/bin/env python3
"""Snapshot GitHub check runs for the commits of a frozen commit/card cohort.

Usage: commit_card_ci.py cohort.json --out new-output-directory
Uses gh's existing authentication; never queries platform gate timestamps.
All check-run pages and reruns are fetched, not just the latest attempt. Completed
checks have bounded observations; running checks have no assumed completion.
Checks are CI activity, not a claim that the agent waited for them or passed CI.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from commit_card_phases import timestamp

REPO = "SageSeekerSociety/cheese"


def fetch_pages(endpoint: str, key: str | None = None) -> list[dict]:
    result = subprocess.run(
        ["gh", "api", "--paginate", "--slurp", endpoint],
        check=True, capture_output=True, text=True,
    )
    pages = json.loads(result.stdout)
    return [row for page in pages for row in (page[key] if key else page)]


def snapshot(path: Path, load) -> dict:
    if path.exists():
        return json.loads(path.read_text())
    data = {"observed_at": datetime.now(timezone.utc).isoformat(), "rows": load()}
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(path)
    return data


def observations(line: dict, runs: list[dict]) -> list[dict]:
    lo, hi = timestamp(line["start"]), timestamp(line["end"])
    out, seen = [], set()
    for run in runs:
        if run["id"] in seen:
            continue
        seen.add(run["id"])
        start, end = run.get("started_at"), run.get("completed_at")
        if not start or timestamp(start) >= hi or (end and timestamp(end) <= lo):
            continue
        # A check skipped without executing is not time spent running a check.
        if run.get("conclusion") in ("skipped", "neutral"):
            continue
        out.append({
            "topic": line["topic"], "source": "github-check:" + str(run["id"]),
            "phase": "ci", "start": start, "end": end,
            "name": run["name"], "head_sha": run["head_sha"],
            "conclusion": run.get("conclusion"),
            "qualification": "check-run-start-to-completion-not-agent-wait",
        })
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cohort", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    cohort = json.loads(args.cohort.read_text())
    args.out.mkdir(parents=True, exist_ok=True)
    all_observations = []
    coverage = []
    for line in cohort["lines"]:
        prs = line["prs"]
        commits = {}
        for pr in prs:
            def load_commits(pr=pr):
                return [{"sha": row["sha"], "committed_at": row["commit"]["committer"]["date"]}
                        for row in fetch_pages(f"repos/{REPO}/pulls/{pr}/commits?per_page=100")]
            data = snapshot(args.out / f"pr-{pr}.json", load_commits)
            commits.update({row["sha"]: row for row in data["rows"]})
        # Use all known PR commits before first card, preserving original window.
        # This cannot recover commits removed by force pushes; report that limit.
        commits = {sha: row for sha, row in commits.items()
                   if timestamp(row["committed_at"]) <= timestamp(line["end"])}
        runs = []
        for sha in sorted(commits):
            def load_runs(sha=sha):
                keys = ("id", "name", "head_sha", "started_at", "completed_at", "conclusion", "status")
                return [{key: row.get(key) for key in keys} for row in fetch_pages(
                    f"repos/{REPO}/commits/{sha}/check-runs?per_page=100&filter=all", "check_runs")]
            data = snapshot(args.out / f"checks-{sha}.json", load_runs)
            runs.extend(data["rows"])
        found = observations(line, runs)
        all_observations.extend(found)
        coverage.append({"task": line["task"], "commits": len(commits),
                         "checks": len({run['id'] for run in runs}), "in_window": len(found)})
        print(f"#{line['task']}: {len(commits)} commits, {len(found)} bounded/unbounded checks", flush=True)
    evidence = {
        "provenance": {"source": "GitHub REST check-runs filter=all, all pages",
                       "cohort_sha256": hashlib.sha256(args.cohort.read_bytes()).hexdigest(),
                       "limitations": "force-pushed-away commits are not recoverable from PR commits"},
        "coverage": coverage, "observations": all_observations,
    }
    (args.out / "evidence.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
