"""Cancel the Required CI runs a merge queue entry has made obsolete.

When the queue is rebuilt — a car ahead is removed or lands — GitHub gives every
pull request behind it a new merge branch, `gh-readonly-queue/main/pr-4097-<sha>`,
and a new `merge_group` event. The sha in that branch name is the commit the
entry sits on (the car ahead), not the entry's own head sha. The run on the old
branch keeps going: Required CI groups merge_group runs by sha, so the new
entry's run does not replace it, and its six jobs hold concurrency slots for a
commit the queue will never merge. queue-dedupe.yml runs this on every
`merge_group` event to cancel them.

A job acts only for the pull request's current queue entry: it first asks
GitHub (GraphQL `pullRequest.mergeQueueEntry.headCommit`) which merge commit
the queue holds for this PR now, and does nothing unless that is this event's
head sha. A late-delivered event of an older entry therefore never cancels the
newer entry's run, whatever order GitHub created the runs in.

The job that does act cancels a run only when all of these hold: it is a
Required CI run of event `merge_group`, not yet completed, its head branch is a
queue branch of the same base and the same pull request number, its head sha
is not the current entry's, and it was created before this entry's own runs.
The last one covers a rebuild that lands between the GraphQL answer and the
cancel: the next entry's runs are newer than this one's.

Nothing here turns red: a ref it cannot read, an API call that fails, a run
object it cannot parse or a run that finished before its cancel arrived is
reported and the job exits 0. A missed cancel costs one superseded run, which
is what happened before this.
"""

import json
import os
import re
import subprocess
import sys
from datetime import datetime

WORKFLOW_FILE = "required-ci.yml"
ACTIVE_STATUSES = ("requested", "pending", "waiting", "queued", "in_progress")
PER_PAGE = 100
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


def active_runs(api, repo):
    """Every not-yet-completed merge_group run of Required CI, all pages."""
    runs = []
    for status in ACTIVE_STATUSES:
        page_number = 1
        while True:
            page = api(
                f"repos/{repo}/actions/workflows/{WORKFLOW_FILE}/runs"
                f"?event=merge_group&status={status}"
                f"&per_page={PER_PAGE}&page={page_number}"
            )
            batch = page["workflow_runs"]
            runs.extend(batch)
            if len(batch) < PER_PAGE:
                break
            page_number += 1
    return runs


CURRENT_ENTRY = """
query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) { mergeQueueEntry { headCommit { oid } } }
  }
}
"""


def current_entry_sha(api, repo, number):
    """The head sha of the PR's merge queue entry right now, or None."""
    owner, name = repo.split("/", 1)
    answer = api(
        "graphql",
        "-f", f"query={CURRENT_ENTRY}",
        "-f", f"owner={owner}",
        "-f", f"name={name}",
        "-F", f"number={number}",
    )
    pull = ((answer or {}).get("data") or {}).get("repository") or {}
    entry = (pull.get("pullRequest") or {}).get("mergeQueueEntry") or {}
    return (entry.get("headCommit") or {}).get("oid")


def cancel_superseded(api, env):
    """Cancel what this entry supersedes; returns the lines to print."""
    head_ref = env.get("HEAD_REF", "")
    head_sha = env.get("HEAD_SHA", "")
    entry = parse_queue_ref(head_ref)
    if entry is None or not head_sha:
        return [f"Not a merge queue ref ({head_ref!r}, sha {head_sha!r}); nothing to cancel."]
    base, number = entry
    repo = env["GITHUB_REPOSITORY"]
    current = current_entry_sha(api, repo, number)
    if current != head_sha:
        return [
            f"PR #{number}'s queue entry is now {current!r}, not {head_sha[:8]}; "
            "this event is not the current entry, nothing to cancel."
        ]
    before = _created(api(f"repos/{repo}/actions/runs/{env['GITHUB_RUN_ID']}"))
    runs = active_runs(api, repo)
    # This entry's own Required CI run is created by the same event; when it is
    # already listed and older than this job, it is the stricter cut-off.
    for run in runs:
        if run.get("head_sha") == head_sha:
            before = min(before, _created(run))
    victims = superseded_runs(runs, head_ref, head_sha, before)
    if not victims:
        return [f"No superseded Required CI run for PR #{number} into {base}."]
    lines = []
    for run in victims:
        try:
            api("-X", "POST", f"repos/{repo}/actions/runs/{run['id']}/cancel")
        except subprocess.CalledProcessError as err:
            # 409: the run finished between the list and the cancel.
            lines.append(f"::warning::Could not cancel run {run['id']}: {err.stderr}")
            continue
        lines.append(
            f"Cancelled Required CI run {run['id']} on {run['head_branch']} "
            f"({run['status']}), superseded by {head_sha[:8]}."
        )
    return lines


def main():
    try:
        lines = cancel_superseded(gh_api, os.environ)
    # Whatever went wrong, a dedupe job must not fail the queue entry.
    except Exception as err:
        detail = getattr(err, "stderr", None) or repr(err)
        lines = [f"::warning::Skipped cancelling superseded runs: {detail}"]
    for line in lines:
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
