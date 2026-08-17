#!/usr/bin/env python3
"""A change to CLAUDE.md needs a human's approval before it can merge.

WHY: CLAUDE.md is loaded in full on every turn of every session, on every
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
"""

import json
import os
import sys
import urllib.error
import urllib.request

REPO = os.environ.get("GITHUB_REPOSITORY", "SageSeekerSociety/cheese")
GUARDED = "CLAUDE.md"
#: A human signal the PR's own author can actually give. GitHub refuses to let
#: an author approve their own PR, and in this repo every PR is opened by the
#: same account the agents act through — so requiring a non-author approval made
#: the file unmodifiable rather than reviewed. What the gate is really after is
#: "a person looked at this", and the person who can least be substituted is the
#: one whose account it went out under.
SIGNOFF_LABEL = "claude-md-ok"


def guards_this_change(paths: list[str]) -> bool:
    """Only the root CLAUDE.md. A nested one (frontend/CLAUDE.md) is scoped to
    its own directory and does not load on every turn everywhere."""
    return GUARDED in paths


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


def _api(path: str) -> list[dict]:
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


def judge(pr: int) -> int:
    files = [f["filename"] for f in _api(f"pulls/{pr}/files?per_page=100")]
    if not guards_this_change(files):
        print(f"CLAUDE.md untouched by #{pr} — gate does not apply.")
        return 0

    detail = _api(f"pulls/{pr}")  # type: ignore[assignment]
    labels = [
        lbl.get("name", "")
        for lbl in (detail.get("labels") or [])  # type: ignore[union-attr]
    ]
    if signed_off(labels):
        print(f"CLAUDE.md change signed off with the {SIGNOFF_LABEL} label.")
        return 0

    author = (detail.get("user") or {}).get("login", "")  # type: ignore[union-attr]
    approvals = human_approvals(_api(f"pulls/{pr}/reviews?per_page=100"), author)
    if approvals:
        print(f"CLAUDE.md change approved by {', '.join(approvals)}.")
        return 0

    print(f"FAIL: #{pr} changes {GUARDED} and no human has signed it off.")
    print("")
    print("  This file is injected into every turn of every session. A wrong line")
    print("  in it steers every agent until a person happens to notice, and")
    print("  nothing else in CI will notice for you.")
    print("")
    print("  Either is enough:")
    print(f"    gh pr edit {pr} --add-label {SIGNOFF_LABEL}      (the author can do this)")
    print(f"    someone other than the author approves #{pr}")
    print("")
    print("  A bot approval (CodeRabbit) does not count either way.")
    print(f"::error::{GUARDED} changed without a human approving review")
    return 1


def self_test() -> int:
    def check(cond: bool, msg: str) -> None:
        if not cond:
            print(f"SELF-TEST FAIL: {msg}")
            sys.exit(1)

    check(guards_this_change(["CLAUDE.md"]), "the root CLAUDE.md must be guarded")
    check(
        not guards_this_change(["docs/README.md", "frontend/CLAUDE.md"]),
        "a nested CLAUDE.md is scoped to its directory and must not trip this",
    )

    check(signed_off([SIGNOFF_LABEL]), "the sign-off label must count")
    check(not signed_off([]), "no label means no sign-off")
    check(not signed_off(["bug", "docs"]), "an unrelated label must not count")

    bot = {"user": {"login": "coderabbitai[bot]", "type": "Bot"}, "state": "APPROVED"}
    author = {"user": {"login": "me", "type": "User"}, "state": "APPROVED"}
    human = {"user": {"login": "lisi", "type": "User"}, "state": "APPROVED"}

    check(human_approvals([human], "me") == ["lisi"], "a human approval must count")
    check(human_approvals([bot], "me") == [], "a bot approval must NOT count")
    check(human_approvals([author], "me") == [], "self-approval must NOT count")
    check(
        human_approvals([bot, author], "me") == [],
        "bot + self must still leave the gate shut",
    )
    # Order matters: the LAST verdict per reviewer wins.
    withdrawn = {"user": {"login": "lisi", "type": "User"}, "state": "CHANGES_REQUESTED"}
    check(
        human_approvals([human, withdrawn], "me") == [],
        "an approval later changed to CHANGES_REQUESTED must not keep the gate open",
    )
    check(
        human_approvals([withdrawn, human], "me") == ["lisi"],
        "and re-approving after that must reopen it",
    )
    commented = {"user": {"login": "lisi", "type": "User"}, "state": "COMMENTED"}
    check(
        human_approvals([human, commented], "me") == ["lisi"],
        "a later comment must not silently revoke an approval",
    )

    print("PASS: check-claude-md-review self-test (scope, label, bots, self, withdrawal)")
    return 0


def main() -> int:
    args = sys.argv[1:]
    if "--self-test" in args:
        return self_test()
    if "--pr" in args:
        try:
            return judge(int(args[args.index("--pr") + 1]))
        except urllib.error.HTTPError as exc:
            print(f"FAIL: GitHub API {exc.code} — cannot verify review state.")
            return 1
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
