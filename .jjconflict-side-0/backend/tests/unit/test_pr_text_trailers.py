"""`Requested-by:` — the handle on the PR body and on the squash commit.

It used to be `Topic.created_by` unconditionally, which on a 分身-split room is
the 分身's own `cheese-<hex12>` handle: PR #500 said `Requested-by:
cheese-c43d2e126d4f`, PR #504 `Requested-by: cheese-a7a0268b96ff`. Neither names
a person. `pr_text` is pure, so the caller resolves the human
(`identity.requester_handle`) and passes it in.
"""

import uuid

from app.domain.review import pr_text
from app.domain.topic.models import Topic
from app.domain.workspace import identity


def _topic(created_by: str | None) -> Topic:
    return Topic(id=uuid.uuid4(), title="做一个东西", created_by=created_by)


def test_the_resolved_human_wins_over_the_agent_that_created_the_room():
    topic = _topic("cheese-a7a0268b96ff")
    trailers = pr_text.pr_trailers(topic, "", None, "alice")
    assert "Requested-by: alice" in trailers
    assert "cheese-a7a0268b96ff" not in trailers


def test_created_by_stays_the_default_for_callers_without_a_session():
    """`pr_text` is reached from paths that have no DB to resolve with; those
    must keep the behaviour they had rather than lose the trailer."""
    assert "Requested-by: bob" in pr_text.pr_trailers(_topic("bob"), "")


def test_no_requester_at_all_omits_the_line():
    trailers = pr_text.pr_trailers(_topic(None), "")
    assert "Requested-by" not in trailers


def test_the_body_and_the_squash_commit_cannot_disagree():
    """Same builder underneath: the PR a reviewer reads and the commit that
    lands on main must name the same person."""
    topic = _topic("cheese-a7a0268b96ff")
    who = identity.GitIdentity("Alice", "583231+alice@users.noreply.github.com")
    body = pr_text.pr_body(topic, "carol", None, who, "alice")
    commit = pr_text.merge_commit_message(topic, "carol", None, who, "alice")
    assert body == commit
    assert "Requested-by: alice" in commit
    assert "Reviewed-by: carol" in commit
    assert "Co-authored-by: Alice <583231+alice@users.noreply.github.com>" in commit
