"""What the platform accepts as the title of the one commit a topic leaves
behind. The format and language are the hosted repository's: a title in any
convention, in any language, is accepted, and only a title the platform cannot
use as a PR title or a commit's first line is refused."""

import uuid
from types import SimpleNamespace

import pytest

from app.core.sentences import in_language
from app.domain.review import notes, nudges, pr_text
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


def test_a_carriage_return_cannot_split_a_commit_title():
    """A bare carriage return is a line break, just like CRLF or LF."""
    subject = "fix: one\rfix: two"
    with pytest.raises(InvalidSubject):
        check_subject(subject)
    assert valid_subject(subject) is None


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


def test_the_pr_title_matches_the_card_only_when_it_is_the_same_string():
    """一个字符串，三个读者。仓库的检查评判 PR 标题，平台落下的也是它 ——
    两者必须逐字相同；差一个字符就是「仓库没评判过这一行」。"""
    topic = _topic("话题")
    line = "fix(review): land the title the checks judged"
    card = AcceptCard(change_subject=line)
    assert pr_text.pr_title_matches_subject(card, topic, line)
    # 首尾空白不算差别 —— GitHub 会 trim，读者也该按同一个字符串比。
    assert pr_text.pr_title_matches_subject(card, topic, f"  {line}\n")
    assert not pr_text.pr_title_matches_subject(card, topic, "fix(review): another")


def test_an_unreadable_pr_title_is_not_a_match():
    """读不出来的标题是「无法确认」，不是「一致」—— 确认正是这道闸的全部意义。
    假的、旧的、或空 payload 都读不出标题，那就不能合。"""
    card = AcceptCard(change_subject="fix: something")
    for absent in (None, "", "   "):
        assert not pr_text.pr_title_matches_subject(card, _topic("话题"), absent)


def test_the_title_nudge_reads_the_same_string_the_merge_gates_do():
    """轮询那条 nudge 要和两道合并闸比同一个字符串。

    只差几个不可见字节的标题（GitHub 原样存着的那一串）在点击合、放行合上是
    「不一致」，会当场把合并拦下。轮询这条路要是先洗再比，同一个 PR 会判成
    「一致」——布防的自动合照合，两条车道两种结论。所以比对用原样的那一串，
    洗净只用来展示。"""
    line = "fix(review): land the title the checks judged"
    card = AcceptCard(
        change_subject=line, pr_number=7, pr_url="https://example.invalid/pull/7"
    )
    topic = _topic("话题")
    status = SimpleNamespace(title=f"{line}\x1b[0m")

    nudge = nudges._title_nudge(card=card, topic=topic, status=status)

    assert nudge is not None
    assert nudge.note_code is notes.NoteCode.pr_title_disagrees
    # 展示的那一行是洗过的：控制字符不许原样送到读的人眼前。
    assert "\x1b" not in nudge.content
    # 洗掉的只是不可见字节，不是标题本身 —— 看得见的差别原样给读者，不然他
    # 根本没法照着改。
    visible = nudges._title_nudge(
        card=card,
        topic=topic,
        status=SimpleNamespace(title="fix(review): lands another line"),
    )
    assert visible is not None
    assert "fix(review): lands another line" in visible.content
    # 逐字相同时不报。
    assert (
        nudges._title_nudge(card=card, topic=topic, status=SimpleNamespace(title=line))
        is None
    )
