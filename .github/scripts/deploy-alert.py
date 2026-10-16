#!/usr/bin/env python3
"""Decide whether a deploy run's deploy job failed, and word the alert if it did.

A red deploy run reached nobody: on 2026-10-14 eight dev deploys failed and the
team's alert group heard about none of them. This runs in its own job on a
hosted runner after the deploy job, because the deploy job cannot report its own
death: when the box's runner loses the server the job fails with no step failed
and nothing after it runs on that runner.

What pages and what does not, by the deploy job's conclusion:

- `failure` pages, with the first step that failed. A job that failed with no
  step failed is a runner that went away mid-deploy, which is the worst case
  for the box, so it pages too.
- `timed_out` pages, and so does a `cancelled` job that ran on a runner for its
  whole timeout: Actions expressions report a timeout as `cancelled`, and a
  deploy that hangs for its full budget is a deploy that did not happen.
- Any other `cancelled` stays quiet. A job that never got a runner was a pending
  run that a newer deploy replaced in the concurrency group (every cancelled dev
  deploy on 2026-10-16 looks like this: no runner, no steps), and one cancelled
  before its timeout was cancelled by a person.
- A job that stopped in a step named in NOT_A_FAILURE_STEPS stays quiet: those
  are a release gate refusing (an approval denied, validation that regressed
  while the run waited), which leaves the deployment as it was.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.request
from datetime import datetime

# A job cancelled this close to its timeout was stopped by the timeout: the
# runner's own clock and the API's timestamps disagree by seconds, not minutes.
TIMEOUT_SLACK_SECONDS = 60


def fetch_jobs(repo: str, run_id: str, attempt: str, token: str) -> list[dict]:
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/actions/runs/{run_id}"
        f"/attempts/{attempt}/jobs?per_page=100",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response).get("jobs", [])


def _seconds(job: dict) -> float:
    start, end = job.get("started_at"), job.get("completed_at")
    if not start or not end:
        return 0.0
    parse = lambda stamp: datetime.fromisoformat(stamp.replace("Z", "+00:00"))  # noqa: E731
    return (parse(end) - parse(start)).total_seconds()


def stopped_at(job: dict) -> str:
    """The step the job ended in: the first that failed, else the first cut off."""
    steps = job.get("steps") or []
    for wanted in ("failure", "cancelled"):
        for step in steps:
            if step.get("conclusion") == wanted:
                return step["name"]
    for step in steps:
        if step.get("status") == "in_progress":
            return step["name"]
    return ""


def assess(job: dict | None, timeout_minutes: int, not_a_failure: set[str]) -> str | None:
    """The alert's reason line, or None when this outcome must not page."""
    if job is None:
        return None
    conclusion = job.get("conclusion")
    step = stopped_at(job)
    if step and step in not_a_failure:
        return None
    if conclusion == "failure":
        if step:
            return f"失败的步骤：{step}"
        return "没有哪一步失败：部署机的 runner 失联，或作业在步骤之外被中止"
    timed_out = conclusion == "timed_out" or (
        conclusion == "cancelled"
        and job.get("runner_name")
        and _seconds(job) >= timeout_minutes * 60 - TIMEOUT_SLACK_SECONDS
    )
    if timed_out:
        where = f"，停在：{step}" if step else ""
        return f"超时：跑满 {timeout_minutes} 分钟被中止{where}"
    return None


def short(commit: str) -> str:
    return commit[:12] if re.fullmatch(r"[0-9a-f]{40}", commit) else commit


def message(environment: str, commit: str, reason: str, run_url: str) -> str:
    # 【严重】 leads because this group also carries the backend's error alerts,
    # and a deploy that did not land has to stand out from them at a glance.
    return (
        f"【严重】部署失败：{environment}\n"
        f"提交：{short(commit) or '未知'}\n"
        f"{reason}\n"
        f"这一轮：{run_url}"
    )


def main() -> int:
    env = os.environ
    jobs = fetch_jobs(env["REPO"], env["RUN_ID"], env["RUN_ATTEMPT"], env["GH_TOKEN"])
    job = next((j for j in jobs if j.get("name") == env.get("DEPLOY_JOB", "deploy")), None)
    not_a_failure = {line.strip() for line in env.get("NOT_A_FAILURE_STEPS", "").splitlines()}
    reason = assess(job, int(env["TIMEOUT_MINUTES"]), not_a_failure - {""})
    if reason is None:
        print(f"deploy job ended {job and job.get('conclusion')}: not a failure to page about")
        return 0
    text = message(env["ENVIRONMENT"], env.get("COMMIT", ""), reason, env["RUN_URL"])
    print(text)
    with open(env["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
        output.write(f"text<<ALERT_EOF\n{text}\nALERT_EOF\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
