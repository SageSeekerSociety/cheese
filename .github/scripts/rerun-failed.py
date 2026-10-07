"""Decide whether a Required CI run may have its failed jobs rerun.

The platform's GitHub App cannot rerun a job itself (it has no actions:write),
so a red run used to mean an empty commit and all 25-35 jobs queued again.
rerun-failed.yml holds actions:write instead and reruns only what failed, when
the App asks with a repository_dispatch; this is the check it runs first.

Reads the run as `GET /repos/{owner}/{repo}/actions/runs/{id}` returns it and
exits non-zero, naming the reason, unless every condition holds.
"""

import json
import sys

WORKFLOW = ".github/workflows/required-ci.yml"
# The first run plus two reruns. A case that is still red after that is not a
# flake a retry will cure.
MAX_ATTEMPTS = 3


def refusal(run: dict, repository: str) -> str | None:
    """Why ``run`` may not be rerun, or None when it may."""
    if (run.get("repository") or {}).get("full_name") != repository:
        return f"run {run.get('id')} is not in {repository}"
    if run.get("path") != WORKFLOW:
        return f"run {run.get('id')} is {run.get('path')!r}, not {WORKFLOW}"
    # A failed merge_group run has already taken its PR out of the queue; a
    # rerun would turn nothing green. The queue's own retry is --reruns.
    if run.get("event") != "pull_request":
        return f"run {run.get('id')} was triggered by {run.get('event')!r}, not a pull request"
    if run.get("status") != "completed" or run.get("conclusion") != "failure":
        return (
            f"run {run.get('id')} is {run.get('status')}/{run.get('conclusion')},"
            " not a completed failure"
        )
    attempt = run.get("run_attempt")
    if not isinstance(attempt, int) or attempt >= MAX_ATTEMPTS:
        return f"run {run.get('id')} is on attempt {attempt}; the limit is {MAX_ATTEMPTS}"
    return None


def main() -> int:
    run = json.load(open(sys.argv[1]))
    reason = refusal(run, sys.argv[2])
    if reason:
        print(f"::error::{reason}")
        return 1
    print(f"Run {run['id']} attempt {run['run_attempt']}: rerunning its failed jobs.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
