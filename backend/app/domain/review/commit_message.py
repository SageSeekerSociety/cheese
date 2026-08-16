"""Conventional Commits for the one commit that lands on main.

The platform squash-merges, so a whole topic collapses into a single commit
whose subject and body the platform writes. That subject used to be
`采纳 <话题标题> (#213)` — the name of a chat room, in Chinese, telling a reader
of `git log` nothing about what changed. This module holds the shape that
replaces it, and the check that keeps it honest.

Why validate at all rather than trust the agent: an unchecked convention is a
convention only until the first busy turn. The rules here are the ones a machine
can decide without guessing at meaning — type prefix, length, no trailing
period, no CJK. Everything a linter cannot see (is the subject actually about
the change? is the body about *why*?) stays in CLAUDE.md, where it is addressed
to the writer instead of the parser.
"""

import re

#: The Conventional Commits v1.0.0 types, plus `revert`. Kept deliberately
#: closed: an open type list is how `misc:` and `update:` creep in.
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
_SUBJECT = re.compile(
    r"^(?P<type>" + "|".join(TYPES) + r")"
    r"(?:\((?P<scope>[^()\s]+)\))?"
    r"(?P<breaking>!)?"
    r": (?P<description>\S.*)$"
)

#: git's own soft limit, and what GitHub truncates at in list views.
MAX_SUBJECT = 72

#: CJK punctuation, unified ideographs, and fullwidth forms — enough to catch a
#: subject written in Chinese without pretending to be a language detector.
_CJK = re.compile(r"[　-〿一-鿿＀-￯]")


class InvalidSubject(ValueError):
    """Carries the sentence shown to whoever filed the card."""


def check_subject(subject: str) -> str:
    """Return the subject unchanged, or raise `InvalidSubject` saying what is
    wrong and what a good one looks like. The examples in the messages are the
    point — an error that only says "invalid" costs a whole turn to act on."""
    subject = subject.strip()
    if not subject:
        raise InvalidSubject("提交标题不能为空")
    if "\n" in subject:
        raise InvalidSubject("提交标题只能有一行；解释写进正文（--body）")
    match = _SUBJECT.match(subject)
    if match is None:
        raise InvalidSubject(
            "提交标题要符合 Conventional Commits：`type(scope): description`，"
            f"type 取值 {', '.join(TYPES)}。例：`fix(accept): keep the PR "
            "branch when a merge conflicts`"
        )
    if len(subject) > MAX_SUBJECT:
        raise InvalidSubject(
            f"提交标题 {len(subject)} 字符，超过 {MAX_SUBJECT}；"
            "把细节挪进正文（--body），标题只说改了什么"
        )
    if subject.endswith("."):
        raise InvalidSubject("提交标题结尾不加句号")
    description = match.group("description")
    if _CJK.search(description):
        raise InvalidSubject(
            "提交标题用英文祈使句（这是要进 git 历史、给仓库所有读者看的）。"
            "话题里照常说中文，只有提交标题和正文是英文"
        )
    # Deliberately NOT enforced: a lowercase first letter. `fix(accept):
    # GitHub token refresh fails` is correct and starts with a capital, and no
    # cheap rule separates that from `Fix the thing` without rejecting real
    # subjects. Case is a style note in CLAUDE.md, not a gate.
    return subject


def valid_subject(subject: str | None) -> str | None:
    """Non-raising variant for read paths that only need to know whether a
    stored subject is still usable."""
    if not subject:
        return None
    try:
        return check_subject(subject)
    except InvalidSubject:
        return None


def merge_subject(subject: str, pr_number: int) -> str:
    """The squash commit's title line. GitHub only auto-appends `(#N)` to the
    DEFAULT title it derives from the repo's `squash_merge_commit_title`
    setting; an explicit `commit_title` replaces that wholesale, so the number
    has to be appended here or the repo's `… (#213)` history style breaks."""
    suffix = f" (#{pr_number})"
    room = MAX_SUBJECT - len(suffix)
    trimmed = subject if len(subject) <= room else f"{subject[: room - 1]}…"
    return f"{trimmed}{suffix}"
