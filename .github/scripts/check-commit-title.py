"""This repository's commit-title convention, checked on the titles that land on main.

Every change reaches main as one squash commit, so that commit's title is all
`git log` says about it. This repo writes it as an English
Conventional Commit (docs/workflows.md §5). The platform leaves a hosted
repository's titles to that repository, this one included, so the check lives
here. It reads titles from its arguments, or one per line on stdin when there
are none, and exits non-zero naming each title that breaks the convention.

`--pull-request FILE` reads a pull request as `GET /repos/{owner}/{repo}/pulls/{n}`
returns it instead. A draft's title is not checked: the platform opens a task's
draft PR under a placeholder (`WIP: <task title>`) and writes the real subject
when it hands the work over and marks the PR ready, and pr-title.yml runs this
check again at that moment.

That placeholder read on a title this check does judge — a PR nobody marked
ready under a subject, or a commit about to land — is named as the platform's
title and answered with the way out, not counted as a format mistake: it is not
the author's text, and the convention has no reading for `WIP:` either way. The
title is still refused.

Not checked, because this repo's own history does not follow it: a length
limit (main's titles routinely run past 72 characters) and a lowercase first
letter (`fix(oauth): GitHub token refresh fails` is correct).

`--warn-only` reports the same findings as warnings and exits 0. The post-submit
Required CI run uses it for the commits a main push landed: they are already on
main, so failing would hold back a validated commit for a formatting mistake
nobody can retitle.
"""

import json
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

#: The platform's placeholder title for a task's draft PR (`WIP: <task title>`,
#: `pr_publish.DRAFT_TITLE_PREFIX`), written when the PR opens and replaced with
#: the change's subject when the work is handed over. Recognised on its own
#: because it is the platform's text, not the author's: judging it by the
#: convention says nothing to whoever has to act on it.
_PLACEHOLDER = re.compile(r"^WIP:(?: |$)")

#: Chinese characters, a run of them, and an English word (two letters or more,
#: so the `a` and `I` of a sentence and the `x` of a variable don't count).
_HAN = re.compile(r"[一-鿿]")
_HAN_RUN = re.compile(r"[一-鿿]+")
_LATIN = re.compile(r"[A-Za-z]")
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9'-]*[A-Za-z0-9]")


def is_english(description: str) -> bool:
    """English, allowing UI strings and product terms in Chinese inside it.

    This repo's English titles name Chinese UI text (`say 暂无改动 only when it
    is true`) and product terms (支线, 芝士), so a long Chinese run alone proves
    nothing. Not English: more Chinese characters than Latin letters, or Chinese
    terms outnumbering the English words around them, which is a Chinese
    sentence naming `GitHub` (`修复 GitHub 的 token 刷新失败`) and not the other
    way round.
    """
    if len(_HAN.findall(description)) > len(_LATIN.findall(description)):
        return False
    return len(_HAN_RUN.findall(description)) <= len(_WORD.findall(description))


EXAMPLE = "fix(accept): keep the PR branch when a merge conflicts"


def stripped(title: str) -> str:
    """The title as the convention reads it: no ` (#123)`, no outer whitespace."""
    return _PR_NUMBER.sub("", title.strip())


def is_placeholder(title: str) -> bool:
    """Whether this is still the platform's own `WIP: <task title>`."""
    return _PLACEHOLDER.match(stripped(title)) is not None


def problems(title: str) -> list[str]:
    title = stripped(title)
    if not title:
        return ["the title is empty"]
    if is_placeholder(title):
        return [
            "still the platform's placeholder title for a task's draft PR"
            " (`WIP: <task title>`), not the subject this change lands under"
        ]
    match = _TITLE.match(title)
    if match is None:
        return [
            "not `type(scope): description` with type one of " + ", ".join(TYPES)
        ]
    found = []
    if title.endswith("."):
        found.append("ends with a period")
    description = match.group("description")
    if not is_english(description):
        found.append("the description is not English")
    return found


#: What this repo's titles are, printed under every refusal.
CONVENTION = (
    "Titles here are English Conventional Commits with no final period "
    "(docs/workflows.md §5); a Chinese product name inside an English "
    f"sentence is fine. Example: {EXAMPLE}. "
    "Fix the PR title, then re-run this job."
)

#: The way out of the one refusal that is not the author's doing, printed
#: alongside the convention only when the platform's placeholder is among the
#: failing titles. It carries the one fact that makes the way out short: an
#: edited title is checked again by itself, so no new commit is needed.
PLACEHOLDER_HINT = (
    "A title above is the platform's placeholder for a task's draft PR: the"
    " platform opens the PR under `WIP: <task title>` and writes the change's"
    " subject over it when the work is handed over, so this PR has none yet."
    " Write the subject this change lands under into the PR title — with REST,"
    " because `gh pr edit` is refused on this repository (its selection still"
    " asks for the retired Projects-classic field):\n"
    "  gh api -X PATCH repos/<owner>/<repo>/pulls/<number> -f title='<subject>'\n"
    "Editing the title runs this check again on its own (pr-title.yml), so no"
    " new commit and no manual re-run are needed."
)


def main(argv: list[str]) -> int:
    warn_only = "--warn-only" in argv
    argv = [argument for argument in argv if argument != "--warn-only"]
    if argv[:1] == ["--pull-request"]:
        with open(argv[1]) as stream:
            pull = json.load(stream)
        if pull.get("draft"):
            print(
                f"::notice::#{pull.get('number')} is a draft; its title is checked"
                " once it is marked ready for review."
            )
            return 0
        argv = [pull.get("title") or ""]
    titles = argv or [line for line in sys.stdin.read().splitlines() if line]
    if not titles:
        print("no title to check", file=sys.stderr)
        return 1
    failed = False
    placeholder = False
    level = "warning" if warn_only else "error"
    for title in titles:
        found = problems(title)
        if found:
            failed = True
            print(f"::{level}::{title!r}: {'; '.join(found)}")
            placeholder = placeholder or is_placeholder(title)
    if failed:
        print(CONVENTION)
        if placeholder:
            print(PLACEHOLDER_HINT)
        if warn_only:
            print("These titles are already on main, so this run reports them and"
                  " does not fail the gate.")
            return 0
        return 1
    print(f"{len(titles)} title(s) follow the convention.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
