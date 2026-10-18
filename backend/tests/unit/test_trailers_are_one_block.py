"""每一条 trailer 都要 git 自己认得出来 —— 用 git 去问，不用字符串包含去问。

一个 commit message 里的 trailer 块**在空行处结束**，而 `git interpret-trailers
--parse` 只解析**最后一个**块。所以在 trailer 之间空一行不是排版，是把它上面的
每一条都丢掉。字符串包含问不出这件事：那行字在消息里，只是 git 不认。
"""

import subprocess
import uuid

import pytest

from app.domain.repository import identity
from app.domain.review import pr_text
from app.domain.topic.models import Topic


def _parse(message: str) -> list[str]:
    """真 git 眼里，这条消息有哪些 trailer。"""
    done = subprocess.run(
        ["git", "interpret-trailers", "--parse"],
        input=message,
        capture_output=True,
        text=True,
    )
    assert done.returncode == 0, done.stderr
    return [line for line in done.stdout.splitlines() if line.strip()]


def _tokens(message: str) -> set[str]:
    return {line.split(":", 1)[0] for line in _parse(message)}


def _message(room, card, who, decided_by="wangchangxin") -> str:
    """The squash commit as it lands: subject, blank line, body."""
    body = pr_text.merge_commit_message(room, decided_by, card, who)
    return f"{card.change_subject}\n\n{body}"


@pytest.fixture
def a_full_delivery():
    room = Topic(id=uuid.uuid4(), project_id=uuid.uuid4(), title="房间")
    card = type(
        "Card",
        (),
        {
            "id": uuid.uuid4(),
            "change_subject": "fix: x",
            "change_body": "为什么这么改。",
        },
    )()
    who = identity.Attribution(
        owner_handle="alice",
        author=None,
        owner=identity.GitIdentity("Alice", "1+alice@users.noreply.github.com"),
        contributors=(identity.GitIdentity("Bob", "2+bob@users.noreply.github.com"),),
        reporters=(identity.platform_identity("carol"),),
        tasks=(identity.WorkItem(task_id=uuid.uuid4(), title="写了这一批"),),
    )
    return room, card, who


def test_git_parses_every_trailer_including_the_co_authors(a_full_delivery):
    room, card, who = a_full_delivery
    assert _tokens(_message(room, card, who)) == {
        "Reported-by",
        "Reviewed-by",
        "Link",
        "Co-authored-by",
    }


def test_a_blank_line_before_the_co_author_would_swallow_the_rest(a_full_delivery):
    """反证，钉住这条规则的原因：把空行加回去，git 就只看得见最后那一段。"""
    room, card, who = a_full_delivery
    good = _message(room, card, who)
    split_apart = good.replace("\nCo-authored-by:", "\n\nCo-authored-by:", 1)
    assert _tokens(split_apart) == {"Co-authored-by"}
    assert _tokens(good) > _tokens(split_apart)


def test_a_credit_the_card_body_repeats_is_parsed_once(a_full_delivery):
    """PR #4272: the card body ended with the agent's own `Co-authored-by`, and
    the platform's block followed it, so the commit named the same person twice."""
    room, card, who = a_full_delivery
    card.change_body = (
        "为什么这么改。\n\nCo-authored-by: Alice <1+alice@users.noreply.github.com>"
    )
    message = _message(room, card, who)
    assert message.count("Co-authored-by: Alice") == 1
    assert "Co-authored-by: Alice <1+alice@users.noreply.github.com>" in _parse(message)


def test_a_feedback_number_from_the_card_body_is_a_trailer_git_reads(
    a_full_delivery,
):
    room, card, who = a_full_delivery
    card.change_body = "为什么这么改。\n\nFixes-feedback: FB-12"
    assert "Fixes-feedback: FB-12" in _parse(_message(room, card, who))


def test_somebody_without_github_gets_the_platforms_own_address(a_full_delivery):
    """降级要诚实：伪造一个 `users.noreply.github.com` 会**看起来**能点开而指向
    谁也不是，比承认「我们没有这个人的账号」更糟。"""
    room, card, _ = a_full_delivery
    who = identity.Attribution("alice", None, owner=identity.platform_identity("alice"))
    parsed = dict(line.split(": ", 1) for line in _parse(_message(room, card, who)))
    assert parsed["Co-authored-by"] == "alice <alice@zhishi.local>"
    assert parsed["Reviewed-by"] == "wangchangxin <wangchangxin@zhishi.local>"
