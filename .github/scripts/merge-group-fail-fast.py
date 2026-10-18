"""Fail a Required CI run's `CI required` the moment one of its jobs fails.

Two events run it: a merge_queue entry and a push to main (the post-submit
gate). Both hold the red until the slowest suite on their own, so both read the
same way below; a pull request does not run it, because `CI required` there is
the merge gate and the aggregator's own report is timely enough.


`CI required` aggregates every suite, so GitHub cannot start it before the
slowest suite has finished. On a merge_group run that meant a job failing at
minute 9 left the entry looking pending until minute 58, and every entry the
queue had built on top of it ran its own doomed run in the meantime (measured
2026-10-17: 3-50 minutes from the first failed job to the red check, 450
minutes in total over one day). The queue removes an entry, and rebuilds the
ones behind it, only once the required check reports.

This runs beside the suites in the same merge_group run, reads the run's jobs
every POLL_SECONDS, and on the first job that concluded `failure` or
`timed_out` posts a completed `CI required` check run with conclusion
`failure` on the entry's head sha. The required check is matched by name and
by the GitHub Actions app, which is the app a check run created with the job's
token belongs to, so the queue reads this one as the required check failing.
The aggregating job still runs when the suites finish and reports the same
failure with every reason; nothing is cancelled, so every suite's result stays
readable for the author.

A failed job always fails `CI required`: no suite job sets job-level
`continue-on-error`, and required-ci.py's judge wants every selected suite to
be `success`. That is what lets one failed job stand for the whole verdict. A
`cancelled` job is not read as a failure here: a job timeout reports as one,
but so does a run the queue-dedupe job cancelled as superseded, and those
entries are no longer in the queue.

The deploy gate (deploy/check-auto-deploy.py) reads the Required CI run's own
conclusion, so a watcher that went red on a green run would hold the commit
back from dev. This script therefore turns its own errors into a warning and
exit 0, bounds every API call, and stops before its job's timeout. What it
cannot rule out is the job failing outside the script (its checkout, a lost
runner); that leaves the entry's verdict untouched and holds back only that
commit's own dev deploy, which the next commit's deploy carries. A missed
fail-fast costs what the queue cost before this existed.

It polls every three minutes and gives up on the first rate-limit answer.
Every workflow in the repository shares the job token's REST budget (1,000
requests an hour per repository), and the steps that spend the rest of it —
`CI required`'s own commit-title read among them — turn red when it runs out.
Up to 16 merge_group runs were in flight at once on 2026-10-17: at a 20-second
interval that is ~2,900 requests an hour, at three minutes 320.
"""

import json
import os
import subprocess
import sys
import time

#: Conclusions that mean the job's own verdict is red.
FAILED = ("failure", "timed_out")
#: Jobs whose result is not a suite's: the aggregators, and this watcher.
NOT_SUITES = ("CI required", "CI required (draft quick)", "Fail fast")
REQUIRED_CHECK = "CI required"
POLL_SECONDS = 180
#: One `gh api` call; a hung one must not outlive the deadline.
CALL_SECONDS = 60
#: The required-ci.yml job allows 130 minutes; stop well inside it.
DEADLINE_SECONDS = 120 * 60


def first_failure(jobs):
    """The earliest-finished failed suite job of ``jobs``, or None."""
    failed = [
        job
        for job in jobs
        if job.get("name") not in NOT_SUITES and job.get("conclusion") in FAILED
    ]
    return min(failed, key=lambda job: job.get("completed_at") or "", default=None)


def watch_over(jobs):
    """Whether the aggregator has started, so it reports any failure itself.

    Not "every suite job has completed": between `scope` finishing and GitHub
    creating a reusable suite's jobs, the jobs listed can all be complete while
    the slowest suites have not appeared yet.
    """
    return any(
        job.get("name") == REQUIRED_CHECK and job.get("status") in ("in_progress", "completed")
        for job in jobs
    )


def failure_check(job, head_sha, run_url, event="merge_group"):
    """The check-run body that fails `CI required` because of ``job``."""
    name = job.get("name", "?")
    what = (
        "this merge queue entry's Required CI run"
        if event == "merge_group"
        else "this main push batch's Required CI run"
    )
    why = (
        "so the queue removes the entry now instead of after the slowest suite"
        if event == "merge_group"
        else "so main reads red now instead of after the slowest suite"
    )
    return {
        "name": REQUIRED_CHECK,
        "head_sha": head_sha,
        "status": "completed",
        "conclusion": "failure",
        "details_url": job.get("html_url") or run_url,
        "output": {
            "title": f"{name} failed",
            "summary": (
                f"`{name}` concluded `{job.get('conclusion')}` in {what} ({run_url})."
                " The rest of the run keeps going, and its `CI required` job lists"
                f" every reason when it finishes; this check is posted early {why}."
            ),
        },
    }


def gh_api(*args, payload=None):
    out = subprocess.run(
        ["gh", "api", *args],
        check=True,
        capture_output=True,
        text=True,
        input=None if payload is None else json.dumps(payload),
        timeout=CALL_SECONDS,
    ).stdout
    return json.loads(out) if out.strip() else None


def run_jobs(api, repo, run_id, attempt):
    jobs = []
    page = 1
    while True:
        batch = api(
            f"repos/{repo}/actions/runs/{run_id}/attempts/{attempt}/jobs"
            f"?per_page=100&page={page}"
        )["jobs"]
        jobs.extend(batch)
        if len(batch) < 100:
            return jobs
        page += 1


def watch(api, env, sleep=time.sleep, clock=time.monotonic):
    """Poll until a job fails (post the check) or the watch is over."""
    repo = env["GITHUB_REPOSITORY"]
    run_id = env["GITHUB_RUN_ID"]
    attempt = env.get("GITHUB_RUN_ATTEMPT", "1")
    head_sha = env["HEAD_SHA"]
    event = env.get("GITHUB_EVENT_NAME", "merge_group")
    run_url = f"{env.get('GITHUB_SERVER_URL', 'https://github.com')}/{repo}/actions/runs/{run_id}"
    started = clock()
    while clock() - started < DEADLINE_SECONDS:
        try:
            jobs = run_jobs(api, repo, run_id, attempt)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as err:
            detail = getattr(err, "stderr", None) or str(err)
            # Polling on would keep the shared budget at zero for everyone.
            if "rate limit" in detail.lower():
                return f"::warning::Stopped: the token's rate limit is used up ({detail.strip()})."
            # One failed read is a network blip; the next poll tries again.
            print(f"::warning::Could not read this run's jobs: {detail}")
            sleep(POLL_SECONDS)
            continue
        job = first_failure(jobs)
        if job is not None:
            api(
                "-X", "POST", f"repos/{repo}/check-runs", "--input", "-",
                payload=failure_check(job, head_sha, run_url, event),
            )
            return f"{job.get('name')} failed: posted a failed {REQUIRED_CHECK} on {head_sha[:8]}."
        if watch_over(jobs):
            return f"No suite failed before {REQUIRED_CHECK} started."
        sleep(POLL_SECONDS)
    return "Stopped at the deadline; the aggregator reports this run."


def main():
    try:
        line = watch(gh_api, os.environ)
    # Whatever went wrong, this job must not turn the run red.
    except Exception as err:
        detail = getattr(err, "stderr", None) or repr(err)
        line = f"::warning::Fail-fast watch stopped: {detail}"
    print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
