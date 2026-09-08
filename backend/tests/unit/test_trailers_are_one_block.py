"""每一条 trailer 都要 git 自己认得出来 —— 用 git 去问，不用字符串包含去问。

一个 commit message 里的 trailer 块**在空行处结束**，而 `git interpret-trailers
--parse` 只解析**最后一个**块。所以「为了好看，在 Co-authored-by 前面空一行」不是
排版，是把它上面的每一条 trailer 都丢掉：`Requested-by` / `Reviewed-by` /
`Cheese-Topic` / `Cheese-Card` / `Cheese-Agent` / `Cheese-Task` 一条都解析不出来。

这个 bug 之所以能溜过所有测试，正是因为测试问的是「这行字面量在不在消息里」——
它在，只是 git 不认。所以这里问的是 git。
"""

import subprocess
import uuid

import pytest

from app.domain.review import pr_text
from app.domain.topic.models import Topic
from app.domain.workspace import identity


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


@pytest.fixture
def a_full_delivery():
    room = Topic(id=uuid.uuid4(), project_id=uuid.uuid4(), title="房间")
    room.created_by = "alice"
    card = type("Card", (), {"id": uuid.uuid4(), "change_body": "为什么这么改。"})()
    who = identity.Attribution(
        handle="alice",
        author=identity.GitIdentity("Alice", "1+alice@users.noreply.github.com"),
        coauthors=(identity.GitIdentity("Bob", "2+bob@users.noreply.github.com"),),
        tasks=(
            identity.WorkItem(
                task_id=uuid.uuid4(), subagent_id="abc123", title="写了这一批"
            ),
        ),
    )
    return room, card, who


def test_git_parses_every_trailer_including_the_co_author(a_full_delivery):
    room, card, who = a_full_delivery

    message = pr_text.local_merge_commit_message(room, "wangchangxin", card, who)

    assert _tokens(message) == {
        "Requested-by",
        "Reviewed-by",
        "Cheese-Topic",
        "Cheese-Card",
        "Cheese-Agent",
        "Cheese-Task",
        "Co-authored-by",
    }


def test_a_blank_line_before_the_co_author_would_swallow_the_rest(a_full_delivery):
    """反证，钉住这条规则的原因：把空行加回去，git 就只看得见最后那一段。

    只有带共同作者的改动才踩得到（没有共同作者就不会插那个空行），所以它躲过了
    历史上每一次交付 —— 也躲过了每一个用「这行字面量在不在」写的断言。
    """
    room, card, who = a_full_delivery
    good = pr_text.local_merge_commit_message(room, "wangchangxin", card, who)

    split_apart = good.replace("\nCo-authored-by:", "\n\nCo-authored-by:")

    assert _tokens(split_apart) == {"Co-authored-by"}
    assert _tokens(good) > _tokens(split_apart)
