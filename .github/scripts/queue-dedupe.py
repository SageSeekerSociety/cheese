"""Cancel the Required CI runs a merge queue entry has made obsolete.

When the queue is rebuilt — a car ahead is removed or lands — GitHub gives every
pull request behind it a new merge branch, `gh-readonly-queue/main/pr-4097-<sha>`,
and a new `merge_group` event. The sha in that branch name is the commit the
entry sits on (the car ahead), not the entry's own head sha. The run on the old
branch keeps going: Required CI groups merge_group runs by sha, so the new
entry's run does not replace it, and its six jobs hold concurrency slots for a
commit the queue will never merge. queue-dedupe.yml runs this on every
`merge_group` event to cancel them.

A run is cancelled only when all of these hold: it is a Required CI run of event
`merge_group`, still queued or in progress, its head branch is a queue branch of
the same base and the same pull request number as this entry, its head sha is
not this entry's, and it was created before this entry's own runs. The last one
keeps a late-delivered event of an older entry from cancelling the newer one.

Nothing here turns red: a ref it cannot read, an API call that fails or a run
that finished before its cancel arrived is reported and the job exits 0. A
missed cancel costs one superseded run, which is what happened before this.
"""

import json
import os
import re
import subprocess
import sys
from datetime import datetime

WORKFLOW_FILE = "required-ci.yml"
ACTIVE_STATUSES = ("queued", "in_progress")
QUEUE_BRANCH = re.compile(
    r"^(?:refs/heads/)?gh-readonly-queue/(?P<base>.+)/pr-(?P<number>\d+)-(?P<base_sha>[0-9a-f]+)$"
)


def parse_queue_ref(ref):
    """`(base, pr_number)` of a merge queue branch or ref, or None."""
    match = QUEUE_BRANCH.match(ref or "")
    if not match:
        return None
    return match["base"], int(match["number"])


def _created(run):
    return datetime.fromisoformat(run["created_at"].replace("Z", "+00:00"))


def superseded_runs(runs, head_ref, head_sha, before):
    """The runs of `runs` that an entry on `head_ref` at `head_sha` replaces.

    `before` is the creation time of this entry's own run; only runs created
    earlier are candidates.
    """
    entry = parse_queue_ref(head_ref)
    if entry is None:
        return []
    picked = []
    for run in runs:
        if run.get("event") != "merge_group":
            continue
        if run.get("status") not in ACTIVE_STATUSES:
            continue
        if parse_queue_ref(run.get("head_branch")) != entry:
            continue
        if run.get("head_sha") == head_sha:
            continue
        if _created(run) >= before:
            continue
        picked.append(run)
    return picked


def gh_api(*args):
    out = subprocess.run(
        ["gh", "api", *args], check=True, capture_output=True, text=True
    ).stdout
    return json.loads(out) if out.strip() else None


def main():
    repo = os.environ["GITHUB_REPOSITORY"]
    head_ref = os.environ.get("HEAD_REF", "")
    head_sha = os.environ.get("HEAD_SHA", "")
    entry = parse_queue_ref(head_ref)
    if entry is None or not head_sha:
        print(f"Not a merge queue ref ({head_ref!r}, sha {head_sha!r}); nothing to cancel.")
        return 0
    base, number = entry
    try:
        this_run = gh_api(f"repos/{repo}/actions/runs/{os.environ['GITHUB_RUN_ID']}")
        before = _created(this_run)
        runs = []
        for status in ACTIVE_STATUSES:
            page = gh_api(
                f"repos/{repo}/actions/workflows/{WORKFLOW_FILE}/runs"
                f"?event=merge_group&status={status}&per_page=100"
            )
            runs.extend(page["workflow_runs"])
    except (subprocess.CalledProcessError, KeyError, ValueError) as err:
        detail = getattr(err, "stderr", "") or err
        print(f"::warning::Could not list Required CI runs: {detail}")
        return 0
    # This entry's own Required CI run is created by the same event; when it is
    # already listed and older than this job, it is the stricter cut-off.
    for run in runs:
        if run.get("head_sha") == head_sha:
            before = min(before, _created(run))
    victims = superseded_runs(runs, head_ref, head_sha, before)
    if not victims:
        print(f"No superseded Required CI run for PR #{number} into {base}.")
        return 0
    for run in victims:
        try:
            gh_api("-X", "POST", f"repos/{repo}/actions/runs/{run['id']}/cancel")
        except subprocess.CalledProcessError as err:
            # 409: the run finished between the list and the cancel.
            print(f"::warning::Could not cancel run {run['id']}: {err.stderr.strip()}")
            continue
        print(
            f"Cancelled Required CI run {run['id']} on {run['head_branch']} "
            f"({run['status']}), superseded by {head_sha[:8]}."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
