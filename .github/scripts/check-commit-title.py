"""This repository's commit-title convention, checked on the titles that land on main.

Every change reaches main as one squash commit through the merge queue, so that
commit's title is all `git log` says about it. This repo writes it as an English
Conventional Commit (docs/workflows.md §5). The platform leaves a hosted
repository's titles to that repository, this one included, so the check lives
here. It reads titles from its arguments, or one per line on stdin when there
are none, and exits non-zero naming each title that breaks the convention.

Not checked, because this repo's own history does not follow it: a length
limit (main's titles routinely run past 72 characters) and a lowercase first
letter (`fix(oauth): GitHub token refresh fails` is correct).
"""

import re
import sys

#: The Conventional Commits v1.0.0 types, plus `revert`. Closed on purpose: an
#: open list is how `misc:` and `update:` creep in.
TYPES = (
    "feat",
    "fix",
    "docs",
    "style",
    "refactor",
    "perf",
    "test",
    "build",
    "ci",
    "chore",
    "revert",
)

#: `type(optional-scope)!: description`. The `!` marks a breaking change.
_TITLE = re.compile(
    r"^(?:" + "|".join(TYPES) + r")(?:\([^()\s]+\))?!?: (?P<description>\S.*)$"
)

#: The ` (#123)` GitHub appends to a squash title: not the author's text.
_PR_NUMBER = re.compile(r" \(#\d+\)$")

#: Chinese characters. A description with more of them than Latin letters is
#: written in Chinese; fewer is an English sentence naming a product term such
#: as 芝士 or 支线, which this repo's titles do and keep doing.
_HAN = re.compile(r"[一-鿿]")
_LATIN = re.compile(r"[A-Za-z]")

EXAMPLE = "fix(accept): keep the PR branch when a merge conflicts"


def problems(title: str) -> list[str]:
    title = _PR_NUMBER.sub("", title.strip())
    if not title:
        return ["the title is empty"]
    match = _TITLE.match(title)
    if match is None:
        return [
            "not `type(scope): description` with type one of " + ", ".join(TYPES)
        ]
    found = []
    if title.endswith("."):
        found.append("ends with a period")
    description = match.group("description")
    if len(_HAN.findall(description)) > len(_LATIN.findall(description)):
        found.append("the description is not English")
    return found


def main(argv: list[str]) -> int:
    titles = argv or [line for line in sys.stdin.read().splitlines() if line]
    if not titles:
        print("no title to check", file=sys.stderr)
        return 1
    failed = False
    for title in titles:
        found = problems(title)
        if found:
            failed = True
            print(f"::error::{title!r}: {'; '.join(found)}")
    if failed:
        print(
            "Titles here are English Conventional Commits with no final period "
            "(docs/workflows.md §5); a Chinese product name inside an English "
            f"sentence is fine. Example: {EXAMPLE}. "
            "Fix the PR title, then re-run this job."
        )
        return 1
    print(f"{len(titles)} title(s) follow the convention.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
