"""A pull request is handed over only with an independent review pinned to its head.

`.claude/scripts/pr-review.sh post` comments on the PR with a first line of
`<!-- pr-review sha=<commit> -->`: one review, of that one commit. The review
means nothing for a head it did not see, and an agent that pushes after pinning
leaves a green-looking PR whose review is of older code. Nothing used to stop
that, so a PR could sit with every check green and still not be acceptable.

The rule, judged on the PR as it is now:

- the NEWEST pin must name the PR's current head, and
- that review must have no `[blocker]` or `[major]` finding.

Only a pin posted by someone with a role on this repository counts
(OWNER/MEMBER/COLLABORATOR): anyone may comment on a public PR, and a comment
that starts with the marker must not be able to supply, or replace, the review.
A draft is not judged (the platform opens a task's draft PR before any review
exists, and marks it ready when it hands the work over); the merge queue does
not run this either, because the PR side has already judged the same commit.

`--pull-request FILE` is `GET /repos/{owner}/{repo}/pulls/{n}`; `--comments FILE`
is `GET /repos/{owner}/{repo}/issues/{n}/comments`, paginated. Exits non-zero
saying what to do.
"""

import json
import re
import sys

MARKER = "<!-- pr-review sha="
#: Associations that make a comment a pin. Matches who can push to this repo.
TRUSTED = ("OWNER", "MEMBER", "COLLABORATOR")
_PIN = re.compile(r"\A<!-- pr-review sha=(?P<sha>[0-9a-f]{40}) -->")
#: A finding heading as `.claude/pr-review/render.py` writes it.
_FINDING = re.compile(r"^### \d+\. \[(?P<severity>blocker|major|minor)\] (?P<title>.*)$", re.M)
BLOCKING = ("blocker", "major")
HOW = "Pin again with .claude/scripts/pr-review.sh prepare <PR>, then post, after the last push."


def _flatten(value):
    if isinstance(value, list):
        for entry in value:
            yield from _flatten(entry)
    else:
        yield value


def load_pages(text: str) -> list[dict]:
    """The comments in a `gh api --paginate` output: one JSON array per page, or one array of pages."""
    decoder = json.JSONDecoder()
    items: list[dict] = []
    at = 0
    while at < len(text):
        if text[at].isspace():
            at += 1
            continue
        page, at = decoder.raw_decode(text, at)
        items.extend(_flatten(page))
    return items


def newest_pin(comments: list[dict]) -> dict | None:
    """The newest trusted comment that opens with the pin marker, oldest-first order kept on ties."""
    pins = [
        c
        for c in comments
        if isinstance(c, dict)
        and c.get("author_association") in TRUSTED
        and isinstance(c.get("body"), str)
        and c["body"].startswith(MARKER)
    ]
    pins.sort(key=lambda c: (c.get("created_at") or "", c.get("id") or 0))
    return pins[-1] if pins else None


def problems(head: str, comments: list[dict]) -> list[str]:
    pin = newest_pin(comments)
    if pin is None:
        return [f"no independent review is pinned on this PR (head {head[:12]}). {HOW}"]
    body = pin["body"]
    match = _PIN.match(body)
    if match is None:
        return [f"the newest review comment has an unreadable pin line. {HOW}"]
    if match["sha"] != head:
        return [
            f"the review is pinned at {match['sha'][:12]}, the PR head is now {head[:12]}. {HOW}"
        ]
    blocking = [
        f"[{f['severity']}] {f['title'].strip()}"
        for f in _FINDING.finditer(body)
        if f["severity"] in BLOCKING
    ]
    if blocking:
        return [
            f"the review of {head[:12]} has {len(blocking)} blocking finding(s): "
            + "; ".join(blocking)
            + ". Fix them, push, and pin a new review."
        ]
    return []


def main(argv: list[str]) -> int:
    if len(argv) != 4 or argv[0] != "--pull-request" or argv[2] != "--comments":
        print(__doc__, file=sys.stderr)
        return 2
    with open(argv[1]) as stream:
        pull = json.load(stream)
    if pull.get("draft"):
        print(
            f"::notice::#{pull.get('number')} is a draft; its review pin is checked"
            " once it is marked ready for review."
        )
        return 0
    head = ((pull.get("head") or {}).get("sha")) or ""
    if not head:
        print("::error::the pull request has no head sha to pin a review to")
        return 1
    with open(argv[3]) as stream:
        comments = load_pages(stream.read())
    found = problems(head, comments)
    for line in found:
        print(f"::error::{line}")
    if found:
        return 1
    print(f"A review is pinned at {head[:12]}, the current head, with no blocker or major finding.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
