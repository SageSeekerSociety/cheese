"""The trailers on the PR body and on the squash commit: who a change belongs to.

`Requested-by:` used to be `Topic.created_by` unconditionally, which on a 分身-split
room is the 分身's own `cheese-<hex12>` handle: PR #500 said `Requested-by:
cheese-c43d2e126d4f`, PR #504 `Requested-by: cheese-a7a0268b96ff`. Neither names a
person. `pr_text` is pure, so the caller resolves the humans
(`identity.attribution`) and passes them in.

`Co-authored-by:` used to name that same person a second time. It now names only
contributors who are NOT the author, which is normally nobody.
"""

import uuid

from app.domain.review import pr_text
from app.domain.topic.models import Topic
from app.domain.workspace import identity

ALICE = identity.GitIdentity("Alice", "583231+alice@users.noreply.github.com")
BOB = identity.GitIdentity("Bob", "42+bob@users.noreply.github.com")


def _topic(created_by: str | None) -> Topic:
    return Topic(id=uuid.uuid4(), title="做一个东西", created_by=created_by)


def test_the_resolved_human_wins_over_the_agent_that_created_the_room():
    topic = _topic("cheese-a7a0268b96ff")
    trailers = pr_text.pr_trailers(topic, "", identity.Attribution("alice", ALICE))
    assert "Requested-by: alice" in trailers
    assert "cheese-a7a0268b96ff" not in trailers


def test_created_by_stays_the_default_for_callers_without_a_session():
    """`pr_text` is reached from paths that have no DB to resolve with; those
    must keep the behaviour they had rather than lose the trailer."""
    assert "Requested-by: bob" in pr_text.pr_trailers(_topic("bob"), "")


def test_no_requester_at_all_omits_the_line():
    trailers = pr_text.pr_trailers(_topic(None), "")
    assert "Requested-by" not in trailers


def test_the_author_is_not_also_listed_as_a_coauthor():
    """The redundancy this removed: a room has ONE git identity, so every commit
    on the branch is already authored by the person `Requested-by` names. A
    trailer pointing at them claimed a second contributor who does not exist.
    `identity.attribution` is what excludes them; nothing here re-adds one."""
    trailers = pr_text.pr_trailers(
        _topic("cheese-a7a0268b96ff"), "carol", identity.Attribution("alice", ALICE)
    )
    assert "Requested-by: alice" in trailers
    assert "Co-authored-by" not in trailers


def test_a_room_that_changed_hands_credits_both_people():
    """归属跟推进者走: bob drove the work so it is attributed to him, and alice —
    who asked for it — is credited on the squash commit rather than vanishing."""
    trailers = pr_text.pr_trailers(
        _topic("cheese-a7a0268b96ff"),
        "carol",
        identity.Attribution("bob", BOB, (ALICE,)),
    )
    assert "Requested-by: bob" in trailers
    assert "Reviewed-by: carol" in trailers
    assert "Co-authored-by: Alice <583231+alice@users.noreply.github.com>" in trailers


def test_every_coauthor_gets_its_own_line_in_the_last_block():
    """git reads trailers from the LAST paragraph, and GitHub reads one
    `Co-authored-by` per line — so more than one credit must not collapse into a
    single line or drift out of that block."""
    trailers = pr_text.pr_trailers(
        _topic(None), "carol", identity.Attribution("dave", None, (ALICE, BOB))
    )
    last_block = trailers.split("\n\n")[-1].splitlines()
    assert last_block == [
        "Co-authored-by: Alice <583231+alice@users.noreply.github.com>",
        "Co-authored-by: Bob <42+bob@users.noreply.github.com>",
    ]


def test_the_platform_itself_is_never_credited():
    """拍板 2026-08-17: every commit here is one 芝士 typed, so the trailer would
    be true of every change and carry no information — and `cheese@zhishi.local`
    links to no account, so it would only pollute the contributor list."""
    trailers = pr_text.pr_trailers(
        _topic(None),
        "",
        identity.Attribution("dave", None, (identity.CHEESE_IDENTITY,)),
    )
    assert "Co-authored-by" not in trailers


def test_the_body_and_the_squash_commit_cannot_disagree():
    """Same builder underneath: the PR a reviewer reads and the commit that
    lands on main must name the same people."""
    topic = _topic("cheese-a7a0268b96ff")
    who = identity.Attribution("bob", BOB, (ALICE,))
    body = pr_text.pr_body(topic, "carol", None, who)
    commit = pr_text.merge_commit_message(topic, "carol", None, who)
    assert body == commit
    assert "Requested-by: bob" in commit
    assert "Reviewed-by: carol" in commit
    assert "Co-authored-by: Alice <583231+alice@users.noreply.github.com>" in commit


def _card(**over) -> "object":
    from app.domain.review.models import AcceptCard

    defaults = {
        "id": uuid.uuid4(),
        "change_subject": "feat: deliver the thing",
        "change_body": "Because it was asked for.",
    }
    defaults.update(over)
    return AcceptCard(**defaults)


def test_the_card_that_delivered_the_change_is_a_trailer_too():
    """#189: `Cheese-Topic` says which room the change came out of; `Cheese-Card`
    says which delivery of that room's work this is. Both lanes write it — the
    GitHub squash body and the platform forge's local squash share this
    builder, so asserting it once covers both."""
    topic = _topic("bob")
    card = _card()
    trailers = pr_text.pr_trailers(topic, "carol", None, card)
    assert f"Cheese-Topic: {topic.id}" in trailers
    assert f"Cheese-Card: {card.id}" in trailers
    assert f"Cheese-Card: {card.id}" in pr_text.pr_body(topic, "carol", card)
    assert f"Cheese-Card: {card.id}" in pr_text.merge_commit_message(
        topic, "carol", card
    )


def test_no_card_no_cheese_card_line():
    """A caller with no card (the legacy subject-less history) must not write a
    trailer pointing at nothing."""
    assert "Cheese-Card" not in pr_text.pr_trailers(_topic("bob"), "carol")


def test_local_merge_commit_message_is_subject_body_then_trailers():
    """The platform forge's squash (#363) takes ONE message: the card's subject
    on the first line, then the same body the GitHub lane writes — no `(#N)`,
    because there is no PR to cite."""
    topic = _topic("bob")
    card = _card()
    msg = pr_text.local_merge_commit_message(
        topic, "carol", card, identity.Attribution("alice", ALICE)
    )
    assert msg.splitlines()[0] == "feat: deliver the thing"
    assert "Because it was asked for." in msg
    assert "Requested-by: alice" in msg
    assert "Reviewed-by: carol" in msg
    assert f"Cheese-Topic: {topic.id}" in msg
    assert f"Cheese-Card: {card.id}" in msg
    assert msg == "feat: deliver the thing\n\n" + pr_text.pr_body(
        topic, "carol", card, identity.Attribution("alice", ALICE)
    )
