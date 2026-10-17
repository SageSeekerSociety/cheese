#!/usr/bin/env python3
"""A change to root AGENTS.md or CLAUDE.md needs sign-off before it can merge.

WHY: AGENTS.md is loaded in full on every turn of every session, on every
machine, for every topic. A bad line there is not one bad file — it is a
standing instruction that every agent obeys until someone notices. And the
noticing is the hard part: nothing goes red, no test fails, the wrong rule just
quietly steers work for weeks. That asymmetry (one edit, unbounded blast radius,
no natural feedback) is what earns this file a reviewer when nothing else in the
repo has one.

WHY A SCRIPT AND NOT BRANCH PROTECTION: this repository is private on a plan
where GitHub refuses both protected branches and CODEOWNERS-required review
("Upgrade to GitHub Pro or make this repository public", HTTP 403). So the gate
is a check that goes red. If the plan ever changes, replace this with the real
thing — a check can be merged past, a protected branch cannot.

BOTS DO NOT COUNT. CodeRabbit reviews every PR in this repo and approves most of
them; counting that would make this gate decorative on day one.

WHAT COUNTS, and why there are two forms: an approving review from someone other
than the author, OR the `claude-md-ok` label. The label exists because the first
form alone made the file unmodifiable — GitHub refuses to let an author approve
their own PR, and every PR here is opened by the account the agents act through,
so there was no move the one person present could make. The gate is not after a
second pair of eyes it cannot get; it is after evidence that a person, rather
than an agent mid-task, decided this line belongs in every future session.

  check-claude-md-review.py --self-test    prove the decision logic
  check-claude-md-review.py --pr <n>       judge a PR (needs GITHUB_TOKEN)
  check-claude-md-review.py --apply-label <n>  record sign-off (authenticated gh)

Checking a PR never adds a label. --apply-label is an explicit sign-off action;
use it only once the instruction change has been reviewed.
"""

import argparse
import json
import os
import shlex
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO = os.environ.get("GITHUB_REPOSITORY", "SageSeekerSociety/cheese")
GUARDED = ("AGENTS.md", "CLAUDE.md")
#: A human signal the PR's own author can actually give. GitHub refuses to let
#: an author approve their own PR, and in this repo every PR is opened by the
#: same account the agents act through — so requiring a non-author approval made
#: the file unmodifiable rather than reviewed. What the gate is really after is
#: "a person looked at this", and the person who can least be substituted is the
#: one whose account it went out under.
SIGNOFF_LABEL = "claude-md-ok"


def guards_this_change(paths: list[str]) -> bool:
    """Only root instruction files; nested files keep their directory scope."""
    return any(path in GUARDED for path in paths)


def signed_off(labels: list[str]) -> bool:
    """Whether a human put the sign-off label on."""
    return SIGNOFF_LABEL in labels


def human_approvals(reviews: list[dict], author: str) -> list[str]:
    """Logins whose APPROVED review counts.

    Reviews arrive oldest-first and a reviewer may submit several; the last
    state per reviewer is the one that counts, so an approval later changed to
    CHANGES_REQUESTED does not keep the gate open.
    """
    latest: dict[str, dict] = {}
    for review in reviews:
        user = review.get("user") or {}
        login = user.get("login", "")
        if not login or user.get("type") == "Bot" or login.endswith("[bot]"):
            continue
        if login == author:
            continue
        # COMMENTED does not overwrite a standing verdict — that is GitHub's own
        # semantics, and without it any comment would silently revoke approval.
        if review.get("state") == "COMMENTED":
            continue
        latest[login] = review
    return sorted(k for k, v in latest.items() if v.get("state") == "APPROVED")


def _api(path: str) -> dict | list[dict]:
    token = os.environ.get("GITHUB_TOKEN", "")
    req = urllib.request.Request(
        f"https://api.github.com/repos/{REPO}/{path}",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "cheese-claude-md-gate",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def _pages(path: str) -> list[dict]:
    rows: list[dict] = []
    page = 1
    while True:
        batch = _api(f"{path}?per_page=100&page={page}")
        if not isinstance(batch, list):
            raise TypeError("GitHub returned a non-list response")
        rows.extend(batch)
        if len(batch) < 100:
            return rows
        page += 1


def label_command(pr: int) -> list[str]:
    # POST adds this label without removing any existing labels. REST also avoids
    # gh pr edit's unrelated GraphQL project lookups and their permission needs.
    return [
        "gh",
        "api",
        "--method",
        "POST",
        f"repos/{REPO}/issues/{pr}/labels",
        "-f",
        f"labels[]={SIGNOFF_LABEL}",
    ]


def apply_label(pr: int) -> int:
    try:
        result = subprocess.run(label_command(pr), timeout=60, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"FAIL: could not add {SIGNOFF_LABEL}: {exc}")
        return 1
    if result.returncode:
        print(f"FAIL: gh could not add {SIGNOFF_LABEL}; check its error above.")
        return 1
    print(f"Added {SIGNOFF_LABEL} to {REPO}#{pr}; wait for the review gate to recheck.")
    return 0


def judge(pr: int) -> int:
    files = [
        path
        for file in _pages(f"pulls/{pr}/files")
        for path in (file["filename"], file.get("previous_filename", ""))
    ]
    if not guards_this_change(files):
        print(f"Root instruction files untouched by #{pr} — gate does not apply.")
        return 0

    changed = ", ".join(path for path in GUARDED if path in files)
    detail = _api(f"pulls/{pr}")
    if not isinstance(detail, dict):
        raise TypeError("GitHub returned a non-object PR response")
    labels = [lbl.get("name", "") for lbl in (detail.get("labels") or [])]
    if signed_off(labels):
        print(f"{changed} change signed off with the {SIGNOFF_LABEL} label.")
        return 0

    author = (detail.get("user") or {}).get("login", "")
    approvals = human_approvals(_pages(f"pulls/{pr}/reviews"), author)
    if approvals:
        print(f"{changed} change approved by {', '.join(approvals)}.")
        return 0

    print(f"FAIL: #{pr} changes {changed} and no human has signed it off.")
    print()
    print("  This file is injected into every turn of every session. A wrong line")
    print("  in it steers every agent until a person happens to notice, and")
    print("  nothing else in CI will notice for you.")
    print()
    print("  Once the instruction change has been reviewed, record sign-off:")
    print(f"    {shlex.join(label_command(pr))}")
    print("  Or use the one-step helper (authenticated gh required):")
    print(
        f"    GITHUB_REPOSITORY={shlex.quote(REPO)} python3 "
        f".claude/scripts/check-claude-md-review.py --apply-label {pr}"
    )
    print(f"  Alternatively, someone other than the author approves #{pr}.")
    print()
    print("  The author can add the label. A bot approval does not count.")
    print(f"::error::{changed} changed without sign-off")
    return 1


def self_test() -> int:
    tests = Path(__file__).with_name("test_check_claude_md_review.py")
    return subprocess.run([sys.executable, str(tests)], check=False).returncode


def positive_pr(value: str) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("PR must be a positive integer") from exc
    if number <= 0:
        raise argparse.ArgumentTypeError("PR must be a positive integer")
    return number


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--self-test", action="store_true")
    action.add_argument("--pr", type=positive_pr)
    action.add_argument(
        "--apply-label",
        type=positive_pr,
        help="explicitly record reviewed instruction changes with the sign-off label",
    )
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test()
    if args.apply_label is not None:
        return apply_label(args.apply_label)
    try:
        return judge(args.pr)
    except urllib.error.HTTPError as exc:
        print(f"FAIL: GitHub API {exc.code} — cannot verify review state.")
    except (urllib.error.URLError, OSError, ValueError, TypeError) as exc:
        print(f"FAIL: cannot verify review state: {exc}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
