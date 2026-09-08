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

from app.domain.identity.handles import topic_agent_handle
from app.domain.review import pr_text
from app.domain.topic.models import Topic
from app.domain.workspace import identity

ALICE = identity.GitIdentity("Alice", "583231+alice@users.noreply.github.com")
BOB = identity.GitIdentity("Bob", "42+bob@users.noreply.github.com")


def _topic(created_by: str | None) -> Topic:
    return Topic(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        title="做一个东西",
        created_by=created_by,
    )


def _room_url(topic: Topic) -> str:
    """#189: `Cheese-Topic` / `Cheese-Card` 是可点开的地址，不是裸 uuid ——
    `git log` 里一个 uuid 是死胡同，除非读的人本来就知道这个平台的路由。"""
    from app.core.config import settings

    base = settings.frontend_url.rstrip("/")
    return f"{base}/projects/{topic.project_id}/topics/{topic.id}"


def _work(subagent_id: str | None, title: str) -> identity.WorkItem:
    return identity.WorkItem(uuid.uuid4(), subagent_id, title)


def _task_url(topic: Topic, item: identity.WorkItem) -> str:
    """哪条活 —— 一个真能打开的地址。`?card=<task_id>` 是房间页面上的一层下钻，
    收的就是 task id（`TopicView.vue` 的 `onOpenCard`）。"""
    return f"{_room_url(topic)}?card={item.task_id}"


def test_the_resolved_human_wins_over_the_agent_that_created_the_room():
    topic = _topic("cheese-a7a0268b96ff")
    trailers = pr_text.pr_trailers(topic, "", identity.Attribution("alice", ALICE))
    assert "Requested-by: Alice <583231+alice@users.noreply.github.com>" in trailers
    assert "cheese-a7a0268b96ff" not in trailers


def test_created_by_stays_the_default_for_callers_without_a_session():
    """`pr_text` is reached from paths that have no DB to resolve with; those
    must keep the behaviour they had rather than lose the trailer."""
    assert "Requested-by: bob <bob@zhishi.local>" in pr_text.pr_trailers(
        _topic("bob"), ""
    )


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
    assert "Requested-by: Alice <583231+alice@users.noreply.github.com>" in trailers
    assert "Co-authored-by" not in trailers


def test_a_room_that_changed_hands_credits_both_people():
    """归属跟推进者走: bob drove the work so it is attributed to him, and alice —
    who asked for it — is credited on the squash commit rather than vanishing."""
    trailers = pr_text.pr_trailers(
        _topic("cheese-a7a0268b96ff"),
        "carol",
        identity.Attribution("bob", BOB, (ALICE,)),
    )
    assert "Requested-by: Bob <42+bob@users.noreply.github.com>" in trailers
    assert "Reviewed-by: carol <carol@zhishi.local>" in trailers
    assert "Co-authored-by: Alice <583231+alice@users.noreply.github.com>" in trailers


def test_every_coauthor_gets_its_own_line_in_the_one_block():
    """GitHub reads one `Co-authored-by` per line, so two credits must not
    collapse into one line — and they must stay in the SAME block as everything
    else. git reads trailers from the last paragraph, so a blank line put here
    to separate the credits would not group them, it would delete every trailer
    above it (see tests/unit/test_trailers_are_one_block.py, which asks git)."""
    trailers = pr_text.pr_trailers(
        _topic(None), "carol", identity.Attribution("dave", None, (ALICE, BOB))
    )
    assert "\n\n" not in trailers
    assert trailers.splitlines()[-2:] == [
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
    assert "Requested-by: Bob <42+bob@users.noreply.github.com>" in commit
    assert "Reviewed-by: carol <carol@zhishi.local>" in commit
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
    assert f"Cheese-Topic: {_room_url(topic)}" in trailers
    assert f"Cheese-Card: {card.id}" in trailers
    assert f"Cheese-Card: {card.id}" in pr_text.pr_body(topic, "carol", card)
    assert f"Cheese-Card: {card.id}" in pr_text.merge_commit_message(
        topic, "carol", card
    )


def test_no_card_no_cheese_card_line():
    """A caller with no card (the legacy subject-less history) must not write a
    trailer pointing at nothing."""
    assert "Cheese-Card" not in pr_text.pr_trailers(_topic("bob"), "carol")


def test_a_delivery_names_the_agent_and_every_worker_behind_it():
    """#189: the commit has to answer WHICH 芝士 wrote this and WHICH work it
    was. `Requested-by` names the human who asked, and `Co-authored-by: Claude
    Fable 5` is on every commit Claude Code writes for anybody anywhere — neither
    can point at the instance of this platform that typed it, nor at the worker
    inside it."""
    topic = _topic("cheese-a7a0268b96ff")
    card = _card()
    one = _work("ac2c038d44616a2f2", "把 trailer 补全")
    two = _work("9f1b7c22e0d341a80", "顺手修一个 flaky 测试")
    trailers = pr_text.pr_trailers(
        topic, "carol", identity.Attribution("alice", ALICE, (), (one, two)), card
    )
    assert trailers.splitlines() == [
        "Requested-by: Alice <583231+alice@users.noreply.github.com>",
        "Reviewed-by: carol <carol@zhishi.local>",
        f"Cheese-Topic: {_room_url(topic)}",
        f"Cheese-Card: {card.id}",
        f"Cheese-Agent: {topic_agent_handle(topic.id)}",
        f"Cheese-Task: {_task_url(topic, one)} ac2c038d44616a2f2 把 trailer 补全",
        f"Cheese-Task: {_task_url(topic, two)} 9f1b7c22e0d341a80 顺手修一个 flaky 测试",
    ]


def test_the_agent_handle_is_the_name_the_room_actually_acts_under():
    """Not a new spelling of the room id: `cheese-<hex12>` is the handle that 分身
    posts under, mints tokens as and keys its memory by, so the name in the commit
    and the name in the room are greppably the same string."""
    topic = _topic("bob")
    assert f"Cheese-Agent: cheese-{topic.id.hex[:12]}" in pr_text.pr_trailers(
        topic, "carol"
    )


def test_work_nobody_was_bound_to_still_gets_a_line():
    """A task row exists from the moment it is dispatched and the worker is bound
    a moment later, so an unbound task is ordinary — and dropping its line would
    make the batch in the commit smaller than the batch that landed."""
    topic = _topic("bob")
    lonely = _work(None, "人自己动手改的")
    trailers = pr_text.pr_trailers(
        topic, "carol", identity.Attribution("alice", ALICE, (), (lonely,))
    )
    assert f"Cheese-Task: {_task_url(topic, lonely)} - 人自己动手改的" in trailers


def test_a_delivery_with_no_work_rows_writes_no_task_lines():
    """A room from before tasks existed still delivers; a trailer pointing at
    nothing would be worse than no trailer."""
    trailers = pr_text.pr_trailers(
        _topic("bob"), "carol", identity.Attribution("alice", ALICE)
    )
    assert "Cheese-Task" not in trailers
    assert "Cheese-Agent" in trailers


def test_a_task_title_cannot_forge_a_trailer():
    """A trailer block ends at the first line that is not a trailer. A title with
    a newline in it would cut everything after it out of the block git and GitHub
    read — and the line it inserted would be indistinguishable from a real one."""
    topic = _topic("bob")
    nasty = _work("ac2c038d44616a2f2", "innocent\nReviewed-by: mallory\n\nmore")
    trailers = pr_text.pr_trailers(
        topic, "carol", identity.Attribution("alice", ALICE, (), (nasty,))
    )
    reviewers = [
        line for line in trailers.splitlines() if line.startswith("Reviewed-by:")
    ]
    assert reviewers == ["Reviewed-by: carol <carol@zhishi.local>"]
    assert (
        f"Cheese-Task: {_task_url(topic, nasty)} ac2c038d44616a2f2 "
        "innocent Reviewed-by: mallory more"
    ) in trailers
    assert len(trailers.splitlines()) == 5  # one line per trailer, no strays


def test_a_very_long_task_title_stays_on_one_readable_line():
    topic = _topic("bob")
    wordy = _work("ac2c038d44616a2f2", "y" * 400)
    line = pr_text.task_trailer(topic, wordy)
    assert line.endswith("y" * 119 + "…")
    assert line in pr_text.pr_trailers(
        topic, "carol", identity.Attribution("alice", ALICE, (), (wordy,))
    )


def test_the_credited_humans_still_come_last_and_stay_in_the_block():
    """The machine trailers must not land between the credits or push one out of
    the block git reads."""
    trailers = pr_text.pr_trailers(
        _topic("bob"),
        "carol",
        identity.Attribution("bob", BOB, (ALICE,), (_work("ac2c038d4", "干活"),)),
    )
    assert "\n\n" not in trailers
    assert trailers.splitlines()[-1] == (
        "Co-authored-by: Alice <583231+alice@users.noreply.github.com>"
    )


def test_both_delivery_lanes_carry_the_agent_and_the_work():
    """The GitHub squash and the platform forge's local squash (#363) share this
    builder, so a reader of `git log` sees the same names whichever lane a project
    landed through."""
    topic = _topic("bob")
    card = _card()
    item = _work("ac2c038d44616a2f2", "把 trailer 补全")
    who = identity.Attribution("alice", ALICE, (), (item,))
    expected = [
        f"Cheese-Agent: {topic_agent_handle(topic.id)}",
        f"Cheese-Task: {_task_url(topic, item)} ac2c038d44616a2f2 把 trailer 补全",
    ]
    github = pr_text.merge_commit_message(topic, "carol", card, who)
    local = pr_text.local_merge_commit_message(topic, "carol", card, who)
    for message in (github, local):
        assert expected == [
            line
            for line in message.splitlines()
            if line.startswith(("Cheese-Agent:", "Cheese-Task:"))
        ]
    assert local == f"{card.change_subject}\n\n{github}"


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
    assert "Requested-by: Alice <583231+alice@users.noreply.github.com>" in msg
    assert "Reviewed-by: carol <carol@zhishi.local>" in msg
    assert f"Cheese-Topic: {_room_url(topic)}" in msg
    assert f"Cheese-Card: {card.id}" in msg
    assert msg == "feat: deliver the thing\n\n" + pr_text.pr_body(
        topic, "carol", card, identity.Attribution("alice", ALICE)
    )


def test_the_pr_body_links_to_the_task_the_card_declared():
    """声明了活的卡，正文里要有一条**打得开**的链接指向那条活。

    这里不比字符串，而是把链接拆开看它指到哪：路径是这个房间的页面，`card` 查询是
    那条活的 id —— 这正是 `?card=` 那层下钻收的东西。一个 uuid 在 PR 正文里是死胡
    同，除非读的人已经知道这个平台的路由。
    """
    from urllib.parse import parse_qs, urlparse

    topic = _topic("bob")
    card = _card()
    item = _work("ac2c038d44616a2f2", "把链接补上")
    body = pr_text.pr_body(
        topic, "carol", card, identity.Attribution("alice", ALICE, (), (item,))
    )
    links = [
        word
        for line in body.splitlines()
        if line.startswith("Cheese-Task:")
        for word in line.split()
        if word.startswith("http")
    ]
    assert len(links) == 1, body
    where = urlparse(links[0])
    assert where.scheme in ("http", "https")
    assert where.path == f"/projects/{topic.project_id}/topics/{topic.id}"
    assert parse_qs(where.query)["card"] == [str(item.task_id)]


def test_a_card_that_declared_no_work_leaves_no_empty_link():
    """没声明活就一条链接都不写。指向空的 `?card=` 是个点开什么都没有的假链接 ——
    比不写更糟，因为它看起来像有东西。"""
    body = pr_text.pr_body(
        _topic("bob"), "carol", _card(), identity.Attribution("alice", ALICE)
    )
    assert "?card=" not in body
    assert "Cheese-Task" not in body
