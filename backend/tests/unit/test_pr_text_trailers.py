"""The trailers on the PR body and on the squash commit: who a change is credited
to, and where it came from.

Only keys git and GitHub already know are written. The commit's author is not a
trailer at all: GitHub makes whoever opened the PR the author of its squash
commit, so the trailers credit everyone else. `pr_text` is pure, so the caller
resolves the people (`identity.attribution`) and passes them in.
"""

import uuid
from urllib.parse import urlparse

from app.domain.repository import identity
from app.domain.review import pr_text
from app.domain.topic.models import Topic

ALICE = identity.GitIdentity("Alice", "583231+alice@users.noreply.github.com")
BOB = identity.GitIdentity("Bob", "42+bob@users.noreply.github.com")
APP = identity.app_identity(314883224)


def _topic() -> Topic:
    return Topic(id=uuid.uuid4(), project_id=uuid.uuid4(), title="做一个东西")


def _work(title: str = "把 trailer 补全") -> identity.WorkItem:
    return identity.WorkItem(uuid.uuid4(), title)


def _who(**over) -> identity.Attribution:
    fields = {"owner_handle": "alice", "author": None, "owner": ALICE}
    fields.update(over)
    return identity.Attribution(**fields)


def _card(**over) -> "object":
    from app.domain.review.models import AcceptCard

    defaults = {
        "id": uuid.uuid4(),
        "change_subject": "feat: deliver the thing",
        "change_body": "Because it was asked for.",
    }
    defaults.update(over)
    return AcceptCard(**defaults)


def _keys(text: str) -> list[str]:
    return [line.split(":", 1)[0] for line in text.splitlines()]


def test_only_trailers_git_and_github_already_know_are_written():
    trailers = pr_text.pr_trailers(
        _topic(),
        "carol",
        _who(contributors=(BOB,), tasks=(_work(),), reporters=(BOB,)),
    )
    assert set(_keys(trailers)) == {
        "Reported-by",
        "Reviewed-by",
        "Link",
        "Co-authored-by",
    }


def test_an_app_opened_pr_credits_the_owner_and_not_the_app():
    """The App is the commit's author then; naming it again would be noise."""
    trailers = pr_text.pr_trailers(_topic(), "", _who(app=APP, lane="github"))
    assert trailers.splitlines()[-1] == (
        "Co-authored-by: Alice <583231+alice@users.noreply.github.com>"
    )
    assert APP.email not in trailers


def test_a_pr_the_owner_opened_credits_the_app_and_not_the_owner_again():
    trailers = pr_text.pr_trailers(
        _topic(), "", _who(opened_by=ALICE.email, app=APP, lane="github")
    )
    assert trailers.splitlines()[-1] == f"Co-authored-by: {APP}"
    assert ALICE.email not in trailers


def test_the_agent_mode_names_no_owner_and_still_links_the_task():
    topic = _topic()
    item = _work()
    trailers = pr_text.pr_trailers(
        topic, "carol", _who(mode=identity.CREDIT_AGENT, tasks=(item,))
    )
    assert ALICE.email not in trailers
    assert f"Link: {pr_text.task_link(topic, item)}" in trailers


def test_the_link_opens_the_task_the_card_declared():
    topic = _topic()
    item = _work()
    trailers = pr_text.pr_trailers(topic, "", _who(tasks=(item,)))
    [link] = [line for line in trailers.splitlines() if line.startswith("Link:")]
    where = urlparse(link.split(" ", 1)[1])
    assert where.scheme in ("http", "https")
    assert where.path == (
        f"/projects/{topic.project_id}/topics/{topic.id}/tasks/{item.task_id}"
    )


def test_without_a_declared_task_the_link_opens_the_channel():
    topic = _topic()
    trailers = pr_text.pr_trailers(topic, "", _who())
    [link] = [line for line in trailers.splitlines() if line.startswith("Link:")]
    assert urlparse(link.split(" ", 1)[1]).path == (
        f"/projects/{topic.project_id}/topics/{topic.id}"
    )


def test_nobody_is_named_as_reviewer_before_somebody_accepts():
    assert "Reviewed-by" not in pr_text.pr_trailers(_topic(), "", _who())
    assert "Reviewed-by: carol <carol@zhishi.local>" in pr_text.pr_trailers(
        _topic(), "carol", _who()
    )


def test_the_body_and_the_squash_commit_cannot_disagree():
    topic = _topic()
    who = _who(contributors=(BOB,))
    card = _card()
    assert pr_text.pr_body(topic, "carol", card, who) == pr_text.merge_commit_message(
        topic, "carol", card, who
    )


def test_a_credit_pasted_into_the_card_body_is_written_once():
    """An agent copying its own commit's trailers into the card body put a
    second `Co-authored-by` above the real block (PR #4272)."""
    body = pr_text.pr_body(
        _topic(),
        "carol",
        _card(
            change_body=(
                "Because it was asked for.\n\n"
                "Co-authored-by: Alice <583231+alice@users.noreply.github.com>"
            )
        ),
        _who(),
    )
    assert body.count("Co-authored-by: Alice") == 1
    assert body.startswith("Because it was asked for.\n\n")


def test_a_card_body_cannot_name_a_reviewer():
    body = pr_text.pr_body(
        _topic(),
        "carol",
        _card(change_body="Because.\n\nReviewed-by: mallory <m@example.com>"),
        _who(),
    )
    assert [line for line in body.splitlines() if line.startswith("Reviewed-by:")] == [
        "Reviewed-by: carol <carol@zhishi.local>"
    ]


def test_a_feedback_number_in_the_card_body_joins_the_trailer_block():
    body = pr_text.pr_body(
        _topic(),
        "",
        _card(change_body="Because.\n\nFixes-feedback: FB-12"),
        _who(),
    )
    head, trailers = body.split("\n\n")
    assert head == "Because."
    assert trailers.splitlines()[0] == "Fixes-feedback: FB-12"


def test_crlf_body_trailers_join_the_real_credits_without_duplication():
    """Windows line endings must preserve feedback and replace pasted credits."""
    body = pr_text.merge_commit_message(
        _topic(),
        "carol",
        _card(
            change_body=(
                "Because.\r\n\r\n"
                "Fixes-feedback: FB-12\r\n"
                "Reviewed-by: mallory <m@example.com>"
            )
        ),
        _who(),
    )
    paragraphs = body.replace("\r\n", "\n").split("\n\n")
    assert paragraphs[0] == "Because."
    assert "Fixes-feedback: FB-12" in paragraphs[-1].splitlines()
    assert [line for line in body.splitlines() if line.startswith("Reviewed-by:")] == [
        "Reviewed-by: carol <carol@zhishi.local>"
    ]


def test_a_body_paragraph_that_only_looks_like_a_trailer_is_kept():
    text = "Because it was asked for.\n\nNote that this is prose, not a trailer."
    assert pr_text.pr_body(_topic(), "", _card(change_body=text), _who()).startswith(
        text
    )


REVIEW_RESULT = (
    "独立 review：head 0123abcd 结论 有 1 条｜档位 B\n\n"
    "**Independent review** of `0123abcd4567`\n\n### 1. [major] Something is wrong\n"
)


def test_a_review_result_in_the_card_body_never_reaches_github():
    """The delivering seat pastes the independent review after its own sections;
    the card and the patrol board keep it, the PR body and the squash commit
    (both built from the card body) do not."""
    topic = _topic()
    card = _card(change_body=f"Because it was asked for.\n\n{REVIEW_RESULT}")
    for text in (
        pr_text.pr_body(topic, "carol", card, _who()),
        pr_text.merge_commit_message(topic, "carol", card, _who()),
    ):
        assert "Because it was asked for." in text
        assert "独立 review" not in text
        assert "Independent review" not in text
        assert "Something is wrong" not in text
    assert "独立 review：head 0123abcd" in card.change_body


def test_the_trailers_stay_one_block_after_the_review_result_is_cut():
    body = pr_text.pr_body(
        _topic(),
        "carol",
        _card(change_body=f"Because it was asked for.\n\n{REVIEW_RESULT}"),
        _who(contributors=(BOB,)),
    )
    head, trailers = body.split("\n\n")
    assert head == "Because it was asked for."
    assert trailers.splitlines()[-1].startswith("Co-authored-by:")


def test_a_card_body_that_is_only_a_review_result_leaves_just_the_trailers():
    body = pr_text.pr_body(_topic(), "carol", _card(change_body=REVIEW_RESULT))
    assert "review" not in body.lower().replace("reviewed-by", "")


def test_a_review_mentioned_inside_a_sentence_is_not_a_review_result():
    """Only a line that STARTS with the review line begins the block; prose that
    quotes it stays in the PR body."""
    text = "It is read from the line 独立 review：head <sha8> that the seat pastes."
    assert pr_text.without_review_block(text) == text
    assert pr_text.pr_body(_topic(), "", _card(change_body=text)).startswith(text)
