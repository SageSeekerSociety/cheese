"""The Conventional Commits gate on the one commit a topic leaves behind.

What these pin down is the *boundary*: which subjects a card can be filed with
and which it cannot. The prose rules that no parser can check (is the subject
about the change? does the body say why?) live in CLAUDE.md — asserting on them
here would only pin the linter's blind spots in place.
"""

import pytest

from app.domain.review.commit_message import (
    MAX_SUBJECT,
    InvalidSubject,
    check_subject,
    merge_subject,
    valid_subject,
)


@pytest.mark.parametrize(
    "subject",
    [
        "fix(accept): open the PR as the requester, not the bot",
        "feat: add cursor pagination to the topic list",
        "chore: snapshot workspace after agent turn",
        "feat(api)!: drop the v1 topic endpoints",
        # Starts with a capital because the WORD is capitalised. Rejecting this
        # is the false positive that a "lowercase the description" rule buys.
        "fix(oauth): GitHub token refresh silently returns None",
    ],
)
def test_accepts_well_formed_subjects(subject):
    assert check_subject(subject) == subject


@pytest.mark.parametrize(
    ("subject", "because"),
    [
        ("修一下分页的 bug", "no type prefix"),
        ("update stuff", "no type prefix"),
        ("misc: tidy things", "type is not in the closed list"),
        ("fix: 修复分页越界", "the description is not English"),
        ("fix: stop the crash.", "trailing period"),
        (f"feat: {'x' * MAX_SUBJECT}", "longer than the subject limit"),
        ("fix: one\nfix: two", "more than one line"),
        ("   ", "empty"),
    ],
)
def test_rejects_malformed_subjects(subject, because):
    with pytest.raises(InvalidSubject):
        check_subject(subject), because


def test_the_rejection_says_what_to_write_instead():
    """An error that only says "invalid" costs a whole turn to act on — the
    example in the message is the part that closes the loop."""
    with pytest.raises(InvalidSubject) as exc:
        check_subject("做完了分页")
    assert "type(scope): description" in str(exc.value)
    assert "fix(accept)" in str(exc.value)


def test_valid_subject_is_the_non_raising_read_path():
    assert valid_subject("fix: stop the crash") == "fix: stop the crash"
    assert valid_subject("做完了") is None
    assert valid_subject(None) is None
    assert valid_subject("") is None


def test_merge_subject_appends_the_pr_number():
    assert merge_subject("fix: stop the crash", 213) == "fix: stop the crash (#213)"


def test_merge_subject_keeps_the_whole_line_inside_the_limit():
    """The number is not optional (GitHub only auto-appends it to titles it
    derived itself), so it is the subject that gives way, not the suffix."""
    line = merge_subject("feat: " + "x" * MAX_SUBJECT, 213)
    assert len(line) <= MAX_SUBJECT
    assert line.endswith("… (#213)")
