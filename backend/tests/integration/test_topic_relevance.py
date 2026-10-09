"""What a channel is to the caller — GET /api/topics{,/{id}} `joined` /
`awaits_me`.

The sidebar lists the channels a person is in and no others, so the list has to
say, per row, whether the caller joined it — a fact about the CALLER, not about
the row. `awaits_me` (a pending card routed to me, an unanswered decision
request, a question only I can answer) says the channel is waiting on me,
whether or not I am in it. Being @-ed is neither: it reaches me through the
notification list.

Everything below asserts on the endpoint's payload — the point is what a
browser receives, not which query produced it. The one exception is the
query-count test, which counts SQL because "correct" and "correct without a
hundred round trips" are separate claims and only one of them is visible in
the JSON.
"""

import uuid

import pytest

from tests.ask_fixtures import active_ask, question_row
from tests.delivery import delivery_headers, delivery_task_id
from tests.integration.conftest import (
    in_thread,
    join_project_team,
    open_task,
    post_message,
    post_project,
    room_socket,
    session_auth_headers,
)


def _project(client, owner: str = "alice") -> str:
    """A project owned by ``owner``, with the whole cast as project members.

    Everyone here has to be a project member just to CALL the endpoint
    (`authorize_project` 403s an outsider), which is what makes 「无关」 a real
    case rather than an authorization failure wearing its clothes.
    """
    pid = post_project(client, json={"name": "P"}, owner=owner).json()["data"]["id"]
    for handle in ("bob", "carol", "dave"):
        join_project_team(client, pid, handle)
    return pid


def _topic(client, pid: str, title: str, created_by: str = "alice") -> str:
    r = client.post(
        "/topics",
        json={"project_id": pid, "title": title},
        headers=session_auth_headers(created_by),
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def _seen_by(client, pid: str, handle: str) -> dict[str, dict]:
    """{title: row} of the project's topics as ``handle`` sees them."""
    r = client.get(
        "/topics",
        params={"project_id": pid},
        headers=session_auth_headers(handle),
    )
    assert r.status_code == 200, r.text
    return {t["title"]: t for t in r.json()["data"]["data"]}


def _card(client, tid: str, reviewer: str) -> str:
    r = client.post(
        f"/topics/{delivery_task_id(client, tid)}/accept-card",
        headers=delivery_headers(client, tid),
        json={
            "change_subject": "chore(test): file an accept card",
            "reviewer_handle": reviewer,
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["status"] == "pending"
    return r.json()["data"]["id"]


def _say(client, tid: str, speaker: str, text: str) -> None:
    """Post a human message, no agent turn — mentions fire on the block persist."""
    with room_socket(client, tid, speaker) as ws:
        post_message(client, tid, speaker, {"content": text})
        while True:
            if ws.receive_json()["type"] in ("done", "error"):
                break


# ---- the four ways a topic can (or cannot) be yours ----------------------


def test_a_person_added_to_a_channel_is_in_it(client):
    """Being put in the channel by its manager is the plain case."""
    pid = _project(client)
    tid = _topic(client, pid, "T", created_by="alice")
    r = client.post(
        f"/topics/{tid}/members",
        json={"handle": "bob", "actor": "alice"},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200, r.text

    row = _seen_by(client, pid, "bob")["T"]
    assert row["joined"] is True
    # Membership alone is not a summons — nothing here is waiting on bob.
    assert row["awaits_me"] is False


def test_the_creator_is_in_the_channel(client):
    pid = _project(client)
    _topic(client, pid, "T", created_by="alice")

    row = _seen_by(client, pid, "alice")["T"]
    assert row["joined"] is True
    assert row["awaits_me"] is False


def test_a_routed_reviewer_is_awaited_without_being_in_the_channel(client):
    """carol was handed a card in a channel she never joined: it is on her desk,
    and that does not put her in the channel."""
    pid = _project(client)
    tid = _topic(client, pid, "T", created_by="alice")
    _card(client, tid, reviewer="carol")

    roster = client.get(f"/topics/{tid}/members").json()["data"]["data"]
    assert "carol" not in {m["member_handle"] for m in roster}

    row = _seen_by(client, pid, "carol")["T"]
    assert row["joined"] is False
    assert row["awaits_me"] is True


def test_an_uninvolved_project_member_relates_to_nothing(client):
    """dave can read the project — he just is not in this channel."""
    pid = _project(client)
    tid = _topic(client, pid, "T", created_by="alice")
    _card(client, tid, reviewer="carol")

    row = _seen_by(client, pid, "dave")["T"]
    assert row["joined"] is False
    assert row["awaits_me"] is False


# ---- @ 提及: participation and the unread override ------------------------


def test_an_at_neither_puts_you_in_the_channel_nor_awaits_you(client):
    """Being @-ed reaches you as a notification. It does not put you in the
    channel, and never lights `awaits_me`, read or not: 芝士 @s people on every
    report and card, and a dot that is always on says nothing."""
    pid = _project(client)
    tid = _topic(client, pid, "T", created_by="alice")
    _say(client, tid, "alice", "<@bob> 看一下这个")

    row = _seen_by(client, pid, "bob")["T"]
    assert row["joined"] is False
    assert row["awaits_me"] is False

    alerts = client.get(
        f"/projects/{pid}/alerts", headers=session_auth_headers("bob")
    ).json()["data"]["data"]
    mention = next(a for a in alerts if a["kind"] == "MENTION")
    assert (
        client.post(
            f"/alerts/{mention['id']}/read", headers=session_auth_headers("bob")
        ).status_code
        == 200
    )

    after = _seen_by(client, pid, "bob")["T"]
    assert after["joined"] is False
    assert after["awaits_me"] is False


def test_an_unanswered_decision_request_awaits_you_until_you_decide(client):
    """A decision request in the room is on your desk until you pick an option.

    Reading it is not deciding it — the same rule the inbox uses."""
    pid = _project(client)
    tid = _topic(client, pid, "T", created_by="alice")
    r = client.post(
        f"/projects/{pid}/alerts",
        json={
            "level": "strong",
            "kind": "decision_request",
            "title": "选哪个",
            "target_handle": "bob",
            "topic_id": tid,
            "payload": {"options": ["A", "B"]},
        },
    )
    assert r.status_code == 200, r.text
    decision = r.json()["data"]["data"][0]

    row = _seen_by(client, pid, "bob")["T"]
    assert row["awaits_me"] is True
    # It waits on bob, not on everyone who can see the room.
    assert _seen_by(client, pid, "alice")["T"]["awaits_me"] is False

    bob = session_auth_headers("bob")
    client.post(f"/alerts/{decision['id']}/read", headers=bob)
    assert _seen_by(client, pid, "bob")["T"]["awaits_me"] is True

    r = client.post(
        f"/alerts/{decision['id']}/resolve", json={"chosen": "A"}, headers=bob
    )
    assert r.status_code == 200, r.text
    after = _seen_by(client, pid, "bob")["T"]
    assert after["awaits_me"] is False


def test_a_decision_asked_on_a_card_awaits_on_its_channel(client):
    """A decision asked inside a task is still that channel waiting on you.

    A channel is the sum of its conversations, and the alert names the task's
    one — the sidebar dot has to light on the channel all the same, and
    clearing the decision has to clear the channel."""
    pid = _project(client)
    tid = _topic(client, pid, "T", created_by="alice")
    card = open_task(client, tid, "报名表单字段精简", owner="alice")
    r = client.post(
        f"/projects/{pid}/alerts",
        json={
            "level": "strong",
            "kind": "decision_request",
            "title": "这个字段删不删",
            "target_handle": "bob",
            "topic_id": card["id"],
            "payload": {"options": ["删", "留"]},
        },
    )
    assert r.status_code == 200, r.text
    decision = r.json()["data"]["data"][0]
    assert decision["topic_id"] == card["id"]

    assert _seen_by(client, pid, "bob")["T"]["awaits_me"] is True

    r = client.post(
        f"/alerts/{decision['id']}/resolve",
        json={"chosen": "删"},
        headers=session_auth_headers("bob"),
    )
    assert r.status_code == 200, r.text
    assert _seen_by(client, pid, "bob")["T"]["awaits_me"] is False


def test_a_question_asked_on_a_card_awaits_on_its_channel(client):
    """芝士在一条活里问的一道题，照样是这个频道在等你回答。

    和上面那条决策请求同一条规矩：一个频道是它全部对话的和，侧栏那一格要亮。题落在
    那条活自己的线上（通知的落点也是它，见 `test_cheese_question_notice`）—— 题在哪
    条线上，和谁在等它，是两件事；少了任务这一头，人就看得见「有一条待办」而侧栏
    一处都不亮。
    """
    pid = _project(client)
    tid = _topic(client, pid, "T", created_by="alice")
    # bob 是这条活的协作者：题问的是他，答它的也是他（任务里只有参与的人说得了话）。
    card = open_task(
        client, tid, "报名表单字段精简", owner="alice", contributors=["bob"]
    )
    question_row(client, card["id"], asked="bob")

    assert _seen_by(client, pid, "bob")["T"]["awaits_me"] is True
    # 题问的是 bob：这个点不为频道里其他任何人亮。
    assert _seen_by(client, pid, "alice")["T"]["awaits_me"] is False

    # 答了就灭：频道等的是那道题，题了结，这一格跟着走。
    _say(client, card["id"], "bob", "按部门")
    assert _seen_by(client, pid, "bob")["T"]["awaits_me"] is False


def _set_card(client, card_id: str, **fields) -> None:
    import asyncio

    from app.domain.review.models import AcceptCard

    async def _run() -> None:
        async with client.test_factory() as s:
            card = await s.get(AcceptCard, uuid.UUID(card_id))
            assert card is not None
            for key, value in fields.items():
                setattr(card, key, value)
            await s.commit()

    asyncio.run(_run())


def test_a_card_whose_checks_failed_is_not_on_the_reviewers_desk(client):
    """CI 挂了，采纳按钮点不了：下一步是芝士去修，不是验收人去点。"""
    pid = _project(client)
    tid = _topic(client, pid, "T", created_by="alice")
    card = _card(client, tid, reviewer="carol")
    _set_card(
        client,
        card,
        note_code="checks_failed",
        merge_state={"state": "blocked", "who": "agent", "reasons": []},
    )

    row = _seen_by(client, pid, "carol")["T"]
    assert row["awaits_me"] is False


def test_a_card_already_approved_and_waiting_to_merge_is_off_the_desk(client):
    """点过采纳、在等合并队列的检查：该点的已经点了，黄灯要灭。"""
    pid = _project(client)
    tid = _topic(client, pid, "T", created_by="alice")
    card = _card(client, tid, reviewer="carol")
    assert _seen_by(client, pid, "carol")["T"]["awaits_me"] is True

    _set_card(
        client,
        card,
        decided_by="carol",
        note_code="waiting_merge_queue",
        merge_state={"state": "blocked", "who": "ci", "reasons": []},
    )
    assert _seen_by(client, pid, "carol")["T"]["awaits_me"] is False


# ---- the fields are per-caller, and per-topic ----------------------------


def test_a_question_waits_on_whoever_summoned_the_agent(
    client, stub_hooks, monkeypatch
):
    """点芝士名的那个人发起了一轮，题就记在他头上 —— 不能谁都不等。

    提问是这轮里发生的，所以「它在回应谁」就是发起这轮的人：题问出口那一刻就
    记在题上（`meta.asked`），不再以后回头去查轮次。
    """
    pid = _project(client)
    tid = _topic(client, pid, "问答", created_by="alice")
    # 芝士在支线里回答，题也在那里问。
    thread = in_thread(client, tid, "alice")
    with active_ask(client, stub_hooks, monkeypatch, thread, actor="bob") as headers:
        r = client.post(
            f"/topics/{thread}/asks",
            json={
                "questions": [
                    {
                        "question": "按哪个口径",
                        "options": [{"text": "按部门"}, {"text": "按项目"}],
                    }
                ]
            },
            headers=headers,
        )
        assert r.status_code == 200, r.text

    assert _seen_by(client, pid, "bob")["问答"]["awaits_me"] is True
    assert _seen_by(client, pid, "carol")["问答"]["awaits_me"] is False


def test_replying_in_words_instead_of_a_button_ends_the_wait(client):
    """没点选项、直接回了一句话，也是回应过了；别人说话不算他回应。

    题上记着等谁（`meta.asked`），所以只有他那句话算数。
    """
    pid = _project(client)
    tid = _topic(client, pid, "问答", created_by="alice")
    # A person asks it, in the channel's main line: an answer wakes no agent.
    question_row(client, tid, author="alice", question="按哪个口径", asked="bob")
    for who in ("bob", "carol"):
        joined = client.post(f"/topics/{tid}/join", headers=session_auth_headers(who))
        assert joined.status_code == 200, joined.text

    _say(client, tid, "carol", "我路过")
    assert _seen_by(client, pid, "bob")["问答"]["awaits_me"] is True

    _say(client, tid, "bob", "都行，你先按部门")
    assert _seen_by(client, pid, "bob")["问答"]["awaits_me"] is False


def test_a_question_awaits_only_whoever_started_the_turn(
    client, stub_hooks, monkeypatch
):
    """芝士停在一道提问上：只有发起那一轮的人能回答，也只有他被等着。

    名册上的其他人照旧看到这个房间，但不该被一道不归他答的题点亮 —— 否则一屋子
    人的侧栏同时亮起同一个点，而能处理它的只有一个。
    """
    pid = _project(client)
    tid = _topic(client, pid, "问答", created_by="alice")
    # 芝士在支线里回答，题也在那里问。
    thread = in_thread(client, tid, "alice")
    with active_ask(client, stub_hooks, monkeypatch, thread, actor="bob") as headers:
        r = client.post(
            f"/topics/{thread}/asks",
            json={
                "questions": [
                    {
                        "question": "按哪个口径",
                        "options": [{"text": "按部门"}, {"text": "按项目"}],
                    }
                ]
            },
            headers=headers,
        )
        assert r.status_code == 200, r.text
    # 芝士问完就收尾（`cheese_ask` 不等回答），这一轮随即关闭——题照样在等 bob。
    bob = _seen_by(client, pid, "bob")["问答"]
    assert bob["awaits_me"] is True
    assert _seen_by(client, pid, "alice")["问答"]["awaits_me"] is False
    assert _seen_by(client, pid, "carol")["问答"]["awaits_me"] is False

    header = client.get(f"/topics/{tid}", headers=session_auth_headers("bob"))
    assert header.status_code == 200, header.text
    assert header.json()["data"]["awaits_me"] is True


def test_two_callers_see_different_answers_for_the_same_topics(client):
    """One list request, two people, two different groupings — the fields are
    about the caller, so a cached/shared answer would be wrong for someone."""
    pid = _project(client)
    _topic(client, pid, "alice 的", created_by="alice")
    bobs = _topic(client, pid, "bob 的", created_by="bob")
    _card(client, bobs, reviewer="carol")

    seen_by_alice = _seen_by(client, pid, "alice")
    assert seen_by_alice["alice 的"]["joined"] is True
    assert seen_by_alice["bob 的"]["joined"] is False

    seen_by_bob = _seen_by(client, pid, "bob")
    assert seen_by_bob["alice 的"]["joined"] is False
    assert seen_by_bob["bob 的"]["joined"] is True

    seen_by_carol = _seen_by(client, pid, "carol")
    assert seen_by_carol["alice 的"]["joined"] is False
    assert seen_by_carol["bob 的"]["awaits_me"] is True


def test_the_topic_header_carries_the_same_verdict(client):
    """`GET /topics/{id}` fills these too — a deep link into a topic that is
    waiting on you must not report `awaits_me: false`."""
    pid = _project(client)
    tid = _topic(client, pid, "T", created_by="alice")
    _card(client, tid, reviewer="carol")

    head = client.get(f"/topics/{tid}", headers=session_auth_headers("carol"))
    assert head.status_code == 200, head.text
    assert head.json()["data"]["joined"] is False
    assert head.json()["data"]["awaits_me"] is True


def test_an_anonymous_caller_gets_the_default(client):
    """No credential = no relationship. The pre-C2 payload, unchanged."""
    pid = _project(client)
    _topic(client, pid, "T", created_by="alice")

    rows = client.get("/topics", params={"project_id": pid}).json()["data"]["data"]
    row = next(t for t in rows if t["title"] == "T")
    assert row["joined"] is False
    assert row["awaits_me"] is False


# ---- N+1 --------------------------------------------------------------


@pytest.fixture
def sql_log(client):
    """Every SQL statement the app runs while the context is open.

    Attached to the engine the TestClient itself is bound to (`client` builds
    its own, per test) and listening at cursor level, so it records what really
    reached the database — including anything a lazy-loading ORM attribute
    would have fired behind the endpoint's back, which is the shape an N+1
    takes. A counter wrapped around the repository would miss exactly that.
    """
    from sqlalchemy import event

    statements: list[str] = []

    def record(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        statements.append(statement)

    engine = client.test_request_factory.kw["bind"].sync_engine
    event.listen(engine, "before_cursor_execute", record)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", record)


def _reads(statements: list[str], table: str) -> int:
    return sum(1 for s in statements if f"FROM {table}" in s)


def test_relevance_costs_constant_queries_whatever_the_project_size(client, sql_log):
    """The hard requirement: the extra cost is CONSTANT, not per topic.

    A project's whole tree comes back in one list call, so a per-topic probe
    would be a hundred round trips to draw one sidebar. Three queries — the
    channels I am in, accept cards, open decision requests — answer it for
    every topic at once.

    Measured at two project sizes in ONE test on purpose: a fixed expected
    number would only pin today's endpoint, while comparing 1 topic against 12
    pins the thing that actually matters — that the count does not move.
    """
    small = _project(client)
    _topic(client, small, "T0", created_by="alice")
    big = _project(client)
    for i in range(12):
        _topic(client, big, f"T{i}", created_by="alice")

    sql_log.clear()
    assert len(_seen_by(client, small, "alice")) == 2  # the topic + 本体 root
    small_log = list(sql_log)

    sql_log.clear()
    assert len(_seen_by(client, big, "alice")) == 13
    big_log = list(sql_log)

    # `notification` is read once: unanswered decision requests (awaiting).
    for table in ("notification",):
        assert _reads(small_log, table) == 1, table
        assert _reads(big_log, table) == 1, table
    # Which channels I am in, and which I manage, are two batched roster
    # reads; adding rooms must not add round trips.
    assert _reads(big_log, "topic_memberships") == _reads(
        small_log, "topic_memberships"
    )
    # `accept_cards` is read TWICE, and the two reads ask different questions
    # that no single scan answers:
    #   - relevance wants "any card here that ever named this viewer" —
    #     every status, because being named is a lasting relationship
    #     (`reviewer_topic_ids`);
    #   - the board wants "the undecided card on this room" — every reviewer,
    #     only live statuses (`_live_cards`).
    # Folding them into one scan means dropping both filters and pulling every
    # card on every listed topic back into Python, which is MORE rows, not
    # fewer round trips. Two is still constant — which is the requirement this
    # test exists for, and the assertion below is what actually enforces it.
    for table in ("accept_cards",):
        assert _reads(small_log, table) == 2, table
        assert _reads(big_log, table) == 2, table
    # And nothing else in the endpoint grew either: twelve times the rows, the
    # same number of round trips.
    assert len(big_log) == len(small_log)


def test_created_room_is_immediately_in_its_creators_sidebar_group(client):
    pid = _project(client)
    response = client.post(
        "/topics",
        json={"project_id": pid, "title": "New"},
        headers=session_auth_headers("alice"),
    )
    assert response.status_code == 200
    created = response.json()["data"]
    assert created["joined"] is True
    assert created["can_manage"] is True
    fetched = client.get(
        f"/topics/{created['id']}", headers=session_auth_headers("alice")
    ).json()["data"]
    assert fetched["joined"] == created["joined"]
