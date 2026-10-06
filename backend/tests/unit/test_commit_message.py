"""What the platform accepts as the title of the one commit a topic leaves
behind. The format and language are the hosted repository's: a title in any
convention, in any language, is accepted, and only a title the platform cannot
use as a PR title or a commit's first line is refused."""

import uuid

import pytest

from app.core.sentences import in_language
from app.domain.review import pr_text
from app.domain.review.commit_message import (
    InvalidSubject,
    check_subject,
    valid_subject,
)
from app.domain.review.models import AcceptCard
from app.domain.topic.models import Topic


@pytest.mark.parametrize(
    "subject",
    [
        "fix(accept): open the PR as the requester, not the bot",
        "修复分页越界",
        "fix: 修复分页越界",
        "Update the README.",
        "[core] Add retry to the uploader",
        "x" * 200,
    ],
)
def test_accepts_a_title_in_any_convention_or_language(subject):
    assert check_subject(subject) == subject


def test_surrounding_whitespace_is_not_part_of_the_title():
    assert check_subject("  修复分页越界 \n") == "修复分页越界"


@pytest.mark.parametrize("subject", ["", "   ", "\n"])
def test_refuses_an_empty_title(subject):
    with pytest.raises(InvalidSubject) as caught:
        check_subject(subject)
    assert caught.value.args[0] == "提交标题不能为空"


def test_refuses_a_title_of_more_than_one_line():
    """A PR title and a commit's first line cannot hold a line break."""
    with pytest.raises(InvalidSubject) as caught:
        check_subject("fix: one\nfix: two")
    said = caught.value.args[0]
    assert in_language(said, "en") == (
        "The commit title can only be one line. Put the explanation in the body"
    )
    assert said == "提交标题只能有一行；解释写进正文（body）"


def test_valid_subject_is_the_non_raising_read_path():
    assert valid_subject("修复分页越界") == "修复分页越界"
    assert valid_subject("a\nb") is None
    assert valid_subject(None) is None
    assert valid_subject("") is None


def _topic(title: str) -> Topic:
    return Topic(id=uuid.uuid4(), project_id=uuid.uuid4(), title=title)


def test_the_merge_title_is_the_whole_subject_and_the_pr_number():
    """A long subject is the repository's to allow: nothing is cut off it."""
    subject = "修复分页越界：" + "很长的说明" * 20
    card = AcceptCard(change_subject=subject)
    title = pr_text.merge_commit_title(card, _topic("话题"), 213)
    assert title == f"{subject} (#213)"


def test_a_card_without_a_subject_falls_back_to_the_topic_title_as_is():
    title = pr_text.merge_commit_title(None, _topic("做一个东西"), 7)
    assert title == "做一个东西 (#7)"


def test_the_fallback_stays_within_a_pr_title():
    """A topic title may run to 300 characters; a GitHub PR title to 256."""
    assert len(pr_text.change_subject(None, _topic("长" * 300))) <= 255
