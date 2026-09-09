"""与我的相关性 — GET /api/topics{,/{id}} `i_participate` / `awaits_me` (C2).

The sidebar lists every topic in a project in one flat stream, so a project
with a dozen threads shows you eleven that are not yours. To fold those away
the frontend has to be told, per row, what the topic is to the person asking —
which is a fact about the CALLER, not about the row, and so cannot live on the
topics table at all.

Two booleans, deliberately not one enum: `i_participate` (roster / creator /
routed reviewer / @'d) is what the fold keys off, and `awaits_me` (a pending
card routed to me, an unread @ at me) is what OVERRIDES the fold, so a topic
waiting on you never ends up hidden inside 「其他话题」.

Everything below asserts on the endpoint's payload — the point is what a
browser receives, not which query produced it. The one exception is the last
test, which counts SQL because "correct" and "correct without a hundred round
trips" are separate claims and only one of them is visible in the JSON.
"""

import pytest

from tests.integration.conftest import chat_ws_url, session_auth_headers


def _project(client, owner: str = "alice") -> str:
    """A project owned by ``owner``, with the whole cast as project members.

    Everyone here has to be a project member just to CALL the endpoint
    (`authorize_project` 403s an outsider), which is what makes 「无关」 a real
    case rather than an authorization failure wearing its clothes.
    """
    pid = client.post("/projects", json={"name": "P", "owner_handle": owner}).json()[
        "data"
    ]["id"]
    for handle in ("bob", "carol", "dave"):
        r = client.post(
            f"/projects/{pid}/members",
            json={"user_handle": handle, "role": "member"},
            headers=session_auth_headers(owner),
        )
        assert r.status_code == 200, r.text
    return pid


def _topic(client, pid: str, title: str, created_by: str = "alice") -> str:
    r = client.post(
        "/topics",
        json={"project_id": pid, "title": title, "created_by": created_by},
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
        f"/topics/{tid}/accept-card",
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
    with client.websocket_connect(chat_ws_url(tid, speaker)) as ws:
        ws.send_json({"type": "message", "content": text, "summon": False})
        while True:
            if ws.receive_json()["type"] in ("done", "error"):
                break


# ---- the four ways a topic can (or cannot) be yours ----------------------


def test_a_roster_member_participates(client):
    """Being in the room is the plain case: added to the roster, nothing else."""
    pid = _project(client)
    tid = _topic(client, pid, "T", created_by="alice")
    r = client.post(
        f"/topics/{tid}/members",
        json={"handle": "bob", "role": "member", "actor": "alice"},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200, r.text

    row = _seen_by(client, pid, "bob")["T"]
    assert row["i_participate"] is True
    # Membership alone is not a summons — nothing here is waiting on bob.
    assert row["awaits_me"] is False


def test_the_creator_participates(client):
    pid = _project(client)
    _topic(client, pid, "T", created_by="alice")

    row = _seen_by(client, pid, "alice")["T"]
    assert row["i_participate"] is True
    assert row["awaits_me"] is False


def test_a_routed_reviewer_participates_and_is_awaited(client):
    """The case the roster cannot answer: carol was handed a card in a room she
    has never been in. Both booleans flip — she is involved, and it is on her
    desk. If only the roster were consulted this topic would fold away."""
    pid = _project(client)
    tid = _topic(client, pid, "T", created_by="alice")
    _card(client, tid, reviewer="carol")

    roster = client.get(f"/topics/{tid}/members").json()["data"]["data"]
    assert "carol" not in {m["member_handle"] for m in roster}

    row = _seen_by(client, pid, "carol")["T"]
    assert row["i_participate"] is True
    assert row["awaits_me"] is True


def test_an_uninvolved_project_member_relates_to_nothing(client):
    """dave can read the project — he just has nothing to do with this topic.

    This is the case the whole feature exists to fold away, so it is also the
    one a too-eager implementation breaks first."""
    pid = _project(client)
    tid = _topic(client, pid, "T", created_by="alice")
    _card(client, tid, reviewer="carol")

    row = _seen_by(client, pid, "dave")["T"]
    assert row["i_participate"] is False
    assert row["awaits_me"] is False


# ---- @ 提及: participation and the unread override ------------------------


def test_an_unread_at_awaits_you_and_a_read_one_still_counts(client):
    """Being @'d makes the topic yours, and stays that way after you read it.

    Read-state moves `awaits_me` only. Otherwise opening the notification would
    make the topic vanish from 「我参与的」 — the same @ that put it there."""
    pid = _project(client)
    tid = _topic(client, pid, "T", created_by="alice")
    _say(client, tid, "alice", "<@bob> 看一下这个")

    row = _seen_by(client, pid, "bob")["T"]
    assert row["i_participate"] is True
    assert row["awaits_me"] is True

    alerts = client.get(
        f"/projects/{pid}/alerts", headers=session_auth_headers("bob")
    ).json()["data"]["data"]
    mention = next(a for a in alerts if a["kind"] == "mention")
    assert (
        client.post(
            f"/alerts/{mention['id']}/read", headers=session_auth_headers("bob")
        ).status_code
        == 200
    )

    after = _seen_by(client, pid, "bob")["T"]
    assert after["i_participate"] is True
    assert after["awaits_me"] is False


# ---- the fields are per-caller, and per-topic ----------------------------


def test_two_callers_see_different_answers_for_the_same_topics(client):
    """One list request, two people, two different groupings — the fields are
    about the caller, so a cached/shared answer would be wrong for someone."""
    pid = _project(client)
    _topic(client, pid, "alice 的", created_by="alice")
    bobs = _topic(client, pid, "bob 的", created_by="bob")
    _card(client, bobs, reviewer="carol")

    seen_by_alice = _seen_by(client, pid, "alice")
    assert seen_by_alice["alice 的"]["i_participate"] is True
    assert seen_by_alice["bob 的"]["i_participate"] is False

    seen_by_bob = _seen_by(client, pid, "bob")
    assert seen_by_bob["alice 的"]["i_participate"] is False
    assert seen_by_bob["bob 的"]["i_participate"] is True

    seen_by_carol = _seen_by(client, pid, "carol")
    assert seen_by_carol["alice 的"]["i_participate"] is False
    assert seen_by_carol["bob 的"]["awaits_me"] is True


def test_the_topic_header_carries_the_same_verdict(client):
    """`GET /topics/{id}` fills these too — a deep link into a topic that is
    waiting on you must not report `awaits_me: false`."""
    pid = _project(client)
    tid = _topic(client, pid, "T", created_by="alice")
    _card(client, tid, reviewer="carol")

    head = client.get(f"/topics/{tid}", headers=session_auth_headers("carol"))
    assert head.status_code == 200, head.text
    assert head.json()["data"]["i_participate"] is True
    assert head.json()["data"]["awaits_me"] is True


def test_an_anonymous_caller_gets_the_default(client):
    """No credential = no relationship. The pre-C2 payload, unchanged."""
    pid = _project(client)
    _topic(client, pid, "T", created_by="alice")

    rows = client.get("/topics", params={"project_id": pid}).json()["data"]["data"]
    row = next(t for t in rows if t["title"] == "T")
    assert row["i_participate"] is False
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

    engine = client.test_factory.kw["bind"].sync_engine
    event.listen(engine, "before_cursor_execute", record)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", record)


def _reads(statements: list[str], table: str) -> int:
    return sum(1 for s in statements if f"FROM {table}" in s)


def test_relevance_costs_three_queries_whatever_the_project_size(client, sql_log):
    """The hard requirement: the extra cost is CONSTANT, not per topic.

    A project's whole tree comes back in one list call, so a per-topic probe
    would be a hundred round trips to draw one sidebar. Three queries — roster,
    accept cards, @-notifications — answer it for every topic at once, and
    「我建的」 is free because `created_by` already rides the rows the endpoint
    fetched anyway.

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

    for table in ("alerts",):
        assert _reads(small_log, table) == 1, table
        assert _reads(big_log, table) == 1, table
    # Archive permission is a separate owner/admin-filtered roster query.
    # Both lookups are batched; adding rooms must not add round trips.
    assert _reads(small_log, "topic_memberships") == 2
    assert _reads(big_log, "topic_memberships") == 2
    # `accept_cards` is read TWICE, and the two reads ask different questions
    # that no single scan answers:
    #   - relevance wants "any card here that ever named this viewer" —
    #     every status, because being named is a lasting relationship
    #     (`reviewer_topic_ids`);
    #   - the board wants "the undecided card on this room" — every reviewer,
    #     only live statuses (`_live_room_cards`).
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
