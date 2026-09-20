#!/usr/bin/env python3
"""Evaluate scheduled heartbeats without treating scheduler gaps as box failures."""

import json
import os
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass
class Assessment:
    errors: list[str]
    warnings: list[str]
    summary: str
    inspect_runs: list[int]


def assess(runs: list[dict], now: datetime) -> Assessment:
    if not runs:
        return Assessment(
            ["No scheduled heartbeats found; check whether the probe is enabled."],
            [],
            "Heartbeat monitoring has no samples. Box health is unknown.",
            [],
        )

    runs = sorted(runs, key=lambda run: run["created_at"], reverse=True)
    latest = runs[0]
    age = (
        now - datetime.fromisoformat(latest["created_at"].replace("Z", "+00:00"))
    ).total_seconds()
    errors, warnings, inspect_runs = [], [], []
    # GitHub can delay or drop scheduled runs. A completed success remains
    # evidence about that run, but cannot establish the box's present health.
    if age > 9300:
        warnings.append(
            f"Latest heartbeat was created {int(age // 60)} minutes ago; "
            "scheduled observations are stale. Current box health is unknown."
        )

    if latest["status"] != "completed" and age > 10800:
        errors.append(
            f"Heartbeat {latest['id']} has remained {latest['status']} for "
            f"{int(age // 60)} minutes. Inspect its jobs and runner availability."
        )
        inspect_runs.append(latest["id"])

    finished = [run for run in runs if run["status"] == "completed"]
    if len(finished) < 2:
        warnings.append(
            "Fewer than two completed heartbeats; "
            "insufficient history to judge repeated failures."
        )
    elif all(run["conclusion"] != "success" for run in finished[:2]):
        errors.append(
            "The last two completed heartbeats were unsuccessful. "
            "Inspect their jobs for the affected runners."
        )
        inspect_runs.extend(run["id"] for run in finished[:2])

    if errors:
        summary = "Heartbeat execution needs attention."
    elif warnings:
        summary = "No execution alert; monitoring coverage is incomplete."
    else:
        summary = "No repeated heartbeat failure observed."
    return Assessment(errors, warnings, summary, list(dict.fromkeys(inspect_runs)))


def api(path: str) -> dict:
    # This runs unattended; a failed API request must fail the monitor rather
    # than substitute an empty history and hide an authentication outage.
    result = subprocess.run(
        ["gh", "api", path], check=True, capture_output=True, text=True
    )
    return json.loads(result.stdout)


def main() -> int:
    repo = os.environ["REPO"]
    response = api(
        f"/repos/{repo}/actions/workflows/box-heartbeat.yml/runs?event=schedule&per_page=20"
    )
    result = assess(response["workflow_runs"], datetime.now(UTC))
    lines = [result.summary]
    for severity, messages in (("warning", result.warnings), ("error", result.errors)):
        for message in messages:
            print(f"::{severity}::{message}")
            lines.append(f"- {message}")
    for run_id in result.inspect_runs:
        lines.append(
            f"- [Heartbeat {run_id}](https://github.com/{repo}/actions/runs/{run_id})"
        )
        jobs = api(f"/repos/{repo}/actions/runs/{run_id}/jobs")["jobs"]
        for job in jobs:
            lines.append(
                f"  - {job['name']}: {job['status']} {job['conclusion'] or ''}"
            )
    report = "\n".join(lines) + "\n"
    print(report)
    if summary_path := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(summary_path, "a") as summary:
            summary.write(report)
    return int(bool(result.errors))


if __name__ == "__main__":
    raise SystemExit(main())
