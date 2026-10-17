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
- A release gate's refusal stays quiet: REFUSED is `true` when the gate said
  so itself (deploy/check-auto-deploy.py's `refused` output, an approver's
  denial), which leaves the deployment as it was. It is not read off the step
  name: the same step fails on a GitHub read that kept dropping or a running
  release the guard cannot place, and those must page.
- A timeout in WAITS_FOR_A_PERSON, the step that waits on an approver, stays
  quiet: nobody approving in time is not a deploy that broke.
- When the job list cannot be read, a deploy job that ended `failure` still
  pages, saying the reason is unknown; a `cancelled` one does not, since most
  of those are runs a newer deploy replaced.
- When the job list has no job named `DEPLOY_JOB`, a run the workflow passed as
  `DEPLOY_RESULT=failure` still pages, naming the missing job: a renamed or
  removed job must not turn a failed deploy into silence.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
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
    # Three tries a few seconds apart: one dropped read must not decide that a
    # failed deploy goes unannounced.
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.load(response).get("jobs", [])
        except (urllib.error.URLError, OSError, ValueError):
            if attempt == 2:
                raise
            time.sleep(5 * (attempt + 1))
    raise AssertionError("unreachable")


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


def assess(job: dict | None, timeout_minutes: int, refused: bool = False,
           waits_for_a_person: str = "") -> str | None:
    """The alert's reason line, or None when this outcome must not page."""
    if job is None or refused:
        return None
    conclusion = job.get("conclusion")
    step = stopped_at(job)
    if conclusion == "failure":
        if step:
            return f"失败的步骤：{step}"
        return "没有哪一步失败：部署机的 runner 失联，或作业在步骤之外被中止"
    timed_out = conclusion == "timed_out" or (
        conclusion == "cancelled"
        and job.get("runner_name")
        and _seconds(job) >= timeout_minutes * 60 - TIMEOUT_SLACK_SECONDS
    )
    if timed_out and step and step == waits_for_a_person:
        return None
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
    try:
        jobs = fetch_jobs(env["REPO"], env["RUN_ID"], env["RUN_ATTEMPT"], env["GH_TOKEN"])
    except (urllib.error.URLError, OSError, ValueError) as error:
        print(f"::warning::could not read this run's jobs: {error}")
        # The gate's verdict needs no API read; a refusal stays quiet here too.
        if env.get("DEPLOY_RESULT") != "failure" or env.get("REFUSED") == "true":
            return 0
        reason = "部署作业失败；读不到是哪一步（GitHub API 不可达），见运行页"
    else:
        job_name = env.get("DEPLOY_JOB", "deploy")
        job = next((j for j in jobs if j.get("name") == job_name), None)
        if job is None:
            # A run the workflow calls a failed deploy whose job list has no deploy
            # job is still a deploy that did not land: the job was renamed or removed,
            # or the run failed before creating it. Page like the unreadable-list
            # branch rather than trust the absent job.
            if env.get("DEPLOY_RESULT") != "failure" or env.get("REFUSED") == "true":
                print(f"no job named {job_name} in this run: not a failure to page about")
                return 0
            reason = f"部署工作流失败，且未找到目标 job `{job_name}`"
        else:
            reason = assess(job, int(env["TIMEOUT_MINUTES"]), env.get("REFUSED") == "true",
                            env.get("WAITS_FOR_A_PERSON", ""))
            if reason is None:
                print(f"deploy job ended {job.get('conclusion')}: not a failure to page about")
                return 0
    text = message(env["ENVIRONMENT"], env.get("COMMIT", ""), reason, env["RUN_URL"])
    print(text)
    with open(env["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
        output.write(f"text<<ALERT_EOF\n{text}\nALERT_EOF\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
