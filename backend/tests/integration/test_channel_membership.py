"""Being in a channel: joining, leaving, speaking in it, managing it, and how
much of it reaches each person.

Everyone in a project reads every public channel. Joining is what puts one in a
person's sidebar and lets them speak in its main line; anyone else answers in a
支线 under a message. 综合 is everyone in the project. A channel is managed by
its creator and by whoever manages the project.
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import update

from tests.integration.conftest import (
    add_external_member,
    join_project_team,
    post_message,
    post_project,
    session_auth_headers,
)


def _project(client) -> dict:
    """dave's project; alice, bob and carol are on its team."""
    p = post_project(client, json={"name": "P"}, owner="dave").json()["data"]
    for handle in ("alice", "bob", "carol"):
        join_project_team(client, p["id"], handle)
    return p


def _channel(client, pid: str, *, by: str = "alice", title: str = "前端") -> str:
    r = client.post(
        "/topics",
        json={"project_id": pid, "title": title},
        headers=session_auth_headers(by),
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def _join(client, tid: str, who: str):
    return client.post(f"/topics/{tid}/join", headers=session_auth_headers(who))


def _leave(client, tid: str, who: str):
    return client.post(f"/topics/{tid}/leave", headers=session_auth_headers(who))


def _row(client, pid: str, tid: str, who: str) -> dict:
    r = client.get(
        "/topics", params={"project_id": pid}, headers=session_auth_headers(who)
    )
    assert r.status_code == 200, r.text
    return next(t for t in r.json()["data"]["data"] if t["id"] == tid)


def _people(client, tid: str) -> set[str]:
    rows = client.get(f"/topics/{tid}/members").json()["data"]["data"]
    return {m["member_handle"] for m in rows if not m["agent"]}


def _say(client, tid: str, who: str, text: str):
    return client.post(
        f"/topics/{tid}/messages",
        json={"request_id": str(uuid.uuid4()), "content": text},
        headers=session_auth_headers(who),
    )


def _thread_under(client, block_id: str, who: str) -> str:
    r = client.post(f"/blocks/{block_id}/thread", headers=session_auth_headers(who))
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def _unread(client, pid: str, tid: str, who: str) -> dict:
    r = client.get(f"/projects/{pid}/topic-unread", headers=session_auth_headers(who))
    assert r.status_code == 200, r.text
    return r.json()["data"].get(tid, {"count": 0, "new": False, "messages": 0})


def _level(client, tid: str, who: str, level: str, until: datetime | None = None):
    body: dict = {"level": level}
    if until is not None:
        body["muted_until"] = until.isoformat()
    return client.put(
        f"/topics/{tid}/notify-level", json=body, headers=session_auth_headers(who)
    )


def _alerts(client, pid: str, who: str, kind: str) -> list[dict]:
    r = client.get(f"/projects/{pid}/alerts", headers=session_auth_headers(who))
    assert r.status_code == 200, r.text
    return [a for a in r.json()["data"]["data"] if a["kind"] == kind]


# ---- joining and leaving ---------------------------------------------------


def test_a_person_joins_and_leaves_a_channel(client):
    p = _project(client)
    tid = _channel(client, p["id"])
    assert _row(client, p["id"], tid, "bob")["joined"] is False

    assert _join(client, tid, "bob").status_code == 200
    assert _row(client, p["id"], tid, "bob")["joined"] is True
    assert "bob" in _people(client, tid)

    assert _leave(client, tid, "bob").status_code == 200
    assert _row(client, p["id"], tid, "bob")["joined"] is False
    assert "bob" not in _people(client, tid)
    # Leaving does not close the door: the channel is still readable.
    r = client.get(f"/topics/{tid}/blocks", headers=session_auth_headers("bob"))
    assert r.status_code == 200, r.text


def test_nobody_leaves_general(client):
    p = _project(client)
    r = _leave(client, p["root_topic_id"], "bob")
    assert r.status_code == 422, r.text
    assert _row(client, p["id"], p["root_topic_id"], "bob")["joined"] is True


def test_an_archived_channel_takes_no_one_new(client):
    p = _project(client)
    tid = _channel(client, p["id"])
    archived = client.post(
        f"/topics/{tid}/archive", headers=session_auth_headers("alice")
    )
    assert archived.status_code == 200, archived.text
    assert _join(client, tid, "bob").status_code == 422
    assert "bob" not in _people(client, tid)


def test_someone_outside_the_project_cannot_join(client):
    p = _project(client)
    tid = _channel(client, p["id"])
    post_project(client, owner="eve")  # eve is someone, just not in this project
    assert _join(client, tid, "eve").status_code == 403
    assert "eve" not in _people(client, tid)


# ---- speaking in the main line ---------------------------------------------


def test_only_a_member_speaks_in_the_main_line(client):
    p = _project(client)
    tid = _channel(client, p["id"])
    said = post_message(client, tid, "alice", {"content": "设计稿出来了"})

    refused = _say(client, tid, "bob", "我也说一句")
    assert refused.status_code == 403, refused.text

    # bob answers under alice's message without joining…
    thread = _thread_under(client, said["id"], "bob")
    replied = _say(client, thread, "bob", "看过了，很好")
    assert replied.status_code == 200, replied.text
    assert _row(client, p["id"], tid, "bob")["joined"] is False

    # …and speaks in the main line once he has joined.
    assert _join(client, tid, "bob").status_code == 200
    assert _say(client, tid, "bob", "我也说一句").status_code == 200


def test_an_archived_channel_is_read_not_spoken_in(client):
    p = _project(client)
    tid = _channel(client, p["id"])
    said = post_message(client, tid, "alice", {"content": "收尾了"})
    thread = _thread_under(client, said["id"], "alice")
    assert _say(client, thread, "alice", "支线里也说一句").status_code == 200
    archived = client.post(
        f"/topics/{tid}/archive", headers=session_auth_headers("alice")
    )
    assert archived.status_code == 200, archived.text

    assert _say(client, tid, "alice", "还能说吗").status_code == 403
    assert _say(client, thread, "alice", "支线呢").status_code == 403
    # The history is still there to read.
    history = client.get(f"/topics/{tid}/blocks", headers=session_auth_headers("bob"))
    assert history.status_code == 200
    assert any(b.get("content") == "收尾了" for b in history.json()["data"]["data"])


def test_everyone_in_the_project_speaks_in_general(client):
    p = _project(client)
    assert _say(client, p["root_topic_id"], "carol", "大家好").status_code == 200


# ---- 综合 is everyone in the project ----------------------------------------


def test_general_is_everyone_in_the_project(client):
    p = _project(client)
    add_external_member(client, p["id"], "erin", by="dave")
    assert _people(client, p["root_topic_id"]) >= {
        "dave",
        "alice",
        "bob",
        "carol",
        "erin",
    }

    # Someone who joins the team later is in 综合 at once, with nothing written.
    join_project_team(client, p["id"], "frank")
    assert "frank" in _people(client, p["root_topic_id"])
    assert _row(client, p["id"], p["root_topic_id"], "frank")["joined"] is True


# ---- who manages a channel --------------------------------------------------


def _rename(client, tid: str, who: str, title: str):
    return client.post(
        f"/topics/{tid}/title", json={"title": title}, headers=session_auth_headers(who)
    )


def _describe(client, tid: str, who: str, text: str):
    return client.put(
        f"/topics/{tid}/description",
        json={"description": text},
        headers=session_auth_headers(who),
    )


def test_the_creator_manages_their_channel(client):
    p = _project(client)
    tid = _channel(client, p["id"], by="alice")
    assert _row(client, p["id"], tid, "alice")["can_manage"] is True
    assert _rename(client, tid, "alice", "前端组").status_code == 200
    described = _describe(client, tid, "alice", "前端的日常")
    assert described.status_code == 200, described.text
    row = _row(client, p["id"], tid, "bob")
    assert (row["title"], row["description"]) == ("前端组", "前端的日常")
    added = client.post(
        f"/topics/{tid}/members",
        json={"handle": "bob"},
        headers=session_auth_headers("alice"),
    )
    assert added.status_code == 200, added.text
    removed = client.delete(
        f"/topics/{tid}/members/bob", headers=session_auth_headers("alice")
    )
    assert removed.status_code == 200, removed.text


def test_whoever_manages_the_project_manages_every_channel(client):
    p = _project(client)
    tid = _channel(client, p["id"], by="alice")
    assert _row(client, p["id"], tid, "dave")["can_manage"] is True
    assert _rename(client, tid, "dave", "前端组").status_code == 200
    assert _describe(client, tid, "dave", "说明").status_code == 200
    added = client.post(
        f"/topics/{tid}/members",
        json={"handle": "carol"},
        headers=session_auth_headers("dave"),
    )
    assert added.status_code == 200, added.text
    archived = client.post(
        f"/topics/{tid}/archive", headers=session_auth_headers("dave")
    )
    assert archived.status_code == 200, archived.text


def test_a_plain_member_does_not_manage_a_channel(client):
    p = _project(client)
    tid = _channel(client, p["id"], by="alice")
    assert _join(client, tid, "bob").status_code == 200
    assert _row(client, p["id"], tid, "bob")["can_manage"] is False

    assert _rename(client, tid, "bob", "改个名").status_code == 403
    assert _describe(client, tid, "bob", "说明").status_code == 403
    assert (
        client.post(
            f"/topics/{tid}/archive", headers=session_auth_headers("bob")
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"/topics/{tid}/members",
            json={"handle": "carol"},
            headers=session_auth_headers("bob"),
        ).status_code
        == 403
    )
    row = _row(client, p["id"], tid, "alice")
    assert (row["title"], row["description"], row["status"]) == ("前端", None, "active")


# ---- handing work to someone puts them in the channel ------------------------


def _task(client, tid: str, by: str = "alice") -> str:
    r = client.post(
        f"/topics/{tid}/tasks",
        json={"title": "做个东西"},
        headers=session_auth_headers(by),
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def test_handing_a_task_to_a_project_person_puts_them_in_the_channel(client):
    p = _project(client)
    tid = _channel(client, p["id"])
    task = _task(client, tid)

    handed = client.patch(
        f"/topics/{task}/task",
        json={"owner_handle": "bob", "contributor_handles": ["carol"]},
        headers=session_auth_headers("alice"),
    )
    assert handed.status_code == 200, handed.text
    assert {"bob", "carol"} <= _people(client, tid)
    assert _row(client, p["id"], tid, "bob")["joined"] is True


def test_work_is_handed_only_to_people_of_the_project(client):
    p = _project(client)
    tid = _channel(client, p["id"])
    task = _task(client, tid)
    post_project(client, owner="eve")

    owner = client.patch(
        f"/topics/{task}/task",
        json={"owner_handle": "eve"},
        headers=session_auth_headers("alice"),
    )
    assert owner.status_code == 422, owner.text
    helper = client.patch(
        f"/topics/{task}/task",
        json={"contributor_handles": ["eve"]},
        headers=session_auth_headers("alice"),
    )
    assert helper.status_code == 422, helper.text
    assert "eve" not in _people(client, tid)


# ---- how much of a channel reaches a person ----------------------------------


def _three_in_a_channel(client) -> tuple[str, str]:
    p = _project(client)
    tid = _channel(client, p["id"], by="alice")
    for who in ("bob", "carol"):
        assert _join(client, tid, who).status_code == 200
    return p["id"], tid


def test_by_default_only_what_is_for_me_is_counted(client):
    pid, tid = _three_in_a_channel(client)
    assert _say(client, tid, "carol", "随便说说").status_code == 200
    plain = _unread(client, pid, tid, "bob")
    assert (plain["count"], plain["new"], plain["messages"]) == (0, True, 1)

    assert _say(client, tid, "carol", "<@bob> 看一下").status_code == 200
    assert _say(client, tid, "carol", "<@all> 开会了").status_code == 200
    counted = _unread(client, pid, tid, "bob")
    assert (counted["count"], counted["messages"]) == (2, 3)


def test_all_new_messages_counts_every_message(client):
    pid, tid = _three_in_a_channel(client)
    assert _level(client, tid, "bob", "all").status_code == 200
    for text in ("一", "二"):
        assert _say(client, tid, "carol", text).status_code == 200
    unread = _unread(client, pid, tid, "bob")
    assert (unread["count"], unread["new"]) == (2, True)


def test_a_muted_channel_counts_only_my_name(client):
    pid, tid = _three_in_a_channel(client)
    assert _level(client, tid, "bob", "mute").status_code == 200
    assert _say(client, tid, "carol", "<@all> 开会了").status_code == 200
    quiet = _unread(client, pid, tid, "bob")
    assert (quiet["count"], quiet["new"], quiet["messages"]) == (0, False, 1)

    assert _say(client, tid, "carol", "<@bob> 你来一下").status_code == 200
    assert _unread(client, pid, tid, "bob")["count"] == 1


def test_a_mute_until_a_time_ends_by_itself(client):
    pid, tid = _three_in_a_channel(client)
    soon = datetime.now(UTC) + timedelta(hours=1)
    assert _level(client, tid, "bob", "mute", soon).status_code == 200
    levels = client.get(
        f"/projects/{pid}/topic-notify-levels", headers=session_auth_headers("bob")
    ).json()["data"]
    assert levels[tid]["level"] == "mute"
    assert levels[tid]["muted_until"] is not None

    # The time passes.
    from app.domain.topic.models import TopicReadState

    async def _expire() -> None:
        async with client.test_factory() as session:
            await session.execute(
                update(TopicReadState)
                .where(
                    TopicReadState.topic_id == uuid.UUID(tid),
                    TopicReadState.user_handle == "bob",
                )
                .values(muted_until=datetime.now(UTC) - timedelta(minutes=1))
            )
            await session.commit()

    asyncio.run(_expire())

    levels = client.get(
        f"/projects/{pid}/topic-notify-levels", headers=session_auth_headers("bob")
    ).json()["data"]
    assert tid not in levels
    assert _say(client, tid, "carol", "<@all> 开会了").status_code == 200
    back = _unread(client, pid, tid, "bob")
    assert (back["count"], back["new"]) == (1, True)
    assert len(_alerts(client, pid, "bob", "MENTION")) == 1


def test_a_level_has_to_be_one_of_the_three_and_a_mute_has_to_end_later(client):
    _, tid = _three_in_a_channel(client)
    assert _level(client, tid, "bob", "loud").status_code == 422
    past = datetime.now(UTC) - timedelta(hours=1)
    assert _level(client, tid, "bob", "mute", past).status_code == 422


def test_at_all_skips_whoever_muted_the_channel(client):
    pid, tid = _three_in_a_channel(client)
    assert _level(client, tid, "bob", "mute").status_code == 200
    assert _say(client, tid, "alice", "<@all> 开会了").status_code == 200
    assert _alerts(client, pid, "bob", "MENTION") == []
    assert len(_alerts(client, pid, "carol", "MENTION")) == 1
    assert _alerts(client, pid, "alice", "MENTION") == []


# ---- replies in a 支线 -------------------------------------------------------


def test_a_reply_in_a_thread_reaches_the_people_who_took_part(client):
    pid, tid = _three_in_a_channel(client)
    said = post_message(client, tid, "alice", {"content": "方案一还是方案二"})
    thread = _thread_under(client, said["id"], "bob")
    assert _say(client, thread, "bob", "方案一").status_code == 200
    # dave never took part; carol is about to.
    assert _say(client, thread, "carol", "我也觉得方案一").status_code == 200

    alice = _alerts(client, pid, "alice", "THREAD_REPLY")
    bob = _alerts(client, pid, "bob", "THREAD_REPLY")
    assert len(alice) == 2  # bob's reply and carol's
    assert len(bob) == 1  # carol's; not his own
    assert _alerts(client, pid, "carol", "THREAD_REPLY") == []
    assert _alerts(client, pid, "dave", "THREAD_REPLY") == []
    # The 支线's replies count on the channel for whoever took part in it.
    assert _unread(client, pid, tid, "bob")["count"] == 1


def test_a_reply_that_names_someone_is_a_mention_not_a_reply(client):
    pid, tid = _three_in_a_channel(client)
    said = post_message(client, tid, "alice", {"content": "方案一还是方案二"})
    thread = _thread_under(client, said["id"], "alice")
    assert _say(client, thread, "bob", "<@alice> 方案一").status_code == 200

    assert _alerts(client, pid, "alice", "THREAD_REPLY") == []
    assert len(_alerts(client, pid, "alice", "MENTION")) == 1


def test_a_muted_channel_sends_no_thread_replies(client):
    pid, tid = _three_in_a_channel(client)
    said = post_message(client, tid, "alice", {"content": "方案一还是方案二"})
    thread = _thread_under(client, said["id"], "alice")
    assert _level(client, tid, "alice", "mute").status_code == 200
    assert _say(client, thread, "bob", "方案一").status_code == 200
    assert _alerts(client, pid, "alice", "THREAD_REPLY") == []


# ---- a project-wide notice ---------------------------------------------------


def test_a_project_wide_notice_reaches_the_team(client):
    p = _project(client)
    r = client.post(
        f"/projects/{p['id']}/alerts",
        json={"level": "light", "kind": "change_alert", "title": "上线了"},
        headers=session_auth_headers("dave"),
    )
    assert r.status_code == 200, r.text
    reached = {row["target_handle"] for row in r.json()["data"]["data"]}
    assert {"dave", "alice", "bob", "carol"} <= reached
