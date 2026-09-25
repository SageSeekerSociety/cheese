"""私聊 (spec §1) + 成员页 (spec §7.2)."""

from tests.integration.conftest import (
    chat_ws_url,
    join_project_team,
    post_project,
    session_auth_headers,
)


def _project(client) -> str:
    return post_project(client, json={"name": "P", "owner_handle": "user-1"}).json()[
        "data"
    ]["id"]


def test_private_chat_get_or_create_and_hidden_from_tree(client):
    pid = _project(client)

    r1 = client.get(f"/projects/{pid}/private-chat?user_handle=user-1")
    assert r1.status_code == 200
    private = r1.json()["data"]
    # Idempotent: same private topic returned.
    r2 = client.get(f"/projects/{pid}/private-chat?user_handle=user-1")
    assert r2.json()["data"]["id"] == private["id"]

    # Two members, like any 1:1: the person, and the project's default
    # teammate under its own seat.
    default = next(
        a
        for a in client.get(f"/projects/{pid}/agents").json()["data"]["data"]
        if a["is_default"]
    )
    members = client.get(
        f"/topics/{private['id']}/members",
        headers=session_auth_headers("user-1"),
    ).json()["data"]["data"]
    assert {m["member_handle"] for m in members} == {"user-1", default["seat_handle"]}

    # Private chat is NOT part of the topic tree.
    tree = client.get(f"/topics?project_id={pid}").json()["data"]["data"]
    assert all(t["id"] != private["id"] for t in tree)

    # It still works as a chat (stub agent answers when summoned). What reaches
    # the room is chat_send's alone, here as in any other room — that contract
    # is pinned in test_chat_publication.py.
    #
    # **不打 @**：私聊是两席的房间，对面那一席是 agent，说话就是对着它说的。那一位
    # 以前是浏览器算好发上来的，现在由服务端自己认（I13）。
    with client.websocket_connect(chat_ws_url(private["id"], "user-1")) as ws:
        ws.send_json({"type": "message", "content": "设个偏好"})
        frames = []
        while True:
            f = ws.receive_json()
            frames.append(f["type"])
            if f["type"] in ("done", "error"):
                break
    assert "event_block" in frames and "error" not in frames


def test_private_human_chat_seeds_both_participants_and_rejects_outsiders(
    client, bearer
):
    pid = _project(client)
    owner_headers = bearer("user-1")
    join_project_team(client, pid, "bob")
    private = client.get(
        f"/projects/{pid}/private-chat",
        params={"user_handle": "user-1", "peer_handle": "alice"},
        headers=owner_headers,
    ).json()["data"]

    members = client.get(
        f"/topics/{private['id']}/members", headers=owner_headers
    ).json()["data"]["data"]
    assert {m["member_handle"] for m in members} == {"alice", "user-1"}

    outsider = bearer("bob")
    assert (
        client.get(f"/topics/{private['id']}/blocks", headers=outsider).status_code
        == 403
    )
    assert (
        client.get(f"/topics/{private['id']}/members", headers=outsider).status_code
        == 403
    )


def test_member_summary(client, bearer):
    pid = _project(client)
    client.post(
        "/topics",
        json={"project_id": pid, "title": "我开的话题", "created_by": "user-1"},
    )
    client.post(
        f"/projects/{pid}/alerts",
        json={
            "level": "light",
            "kind": "decision_request",
            "title": "等你定",
            "target_handle": "user-1",
        },
    )

    # The member page resolves the viewer; personal waiting items show only on
    # the member's own page, so read it as user-1.
    s = client.get(
        f"/projects/{pid}/members/user-1/summary", headers=bearer("user-1")
    ).json()["data"]
    assert s["handle"] == "user-1"
    # user-1 is in the project as its owner.
    assert s["source"] == "owner"
    assert any(t["title"] == "我开的话题" for t in s["topics_started"])
    assert any(w["title"] == "等你定" for w in s["waiting_on_you"])


def test_a_dm_between_two_people_summons_nobody(client, bearer):
    """人和人的私聊也是 `is_private`，但它没有一席 agent 可点名。

    「私聊 ⇒ 点了名」这个判据会把芝士叫进两个人的私密对话里说话：那种房间两席都是
    人，`AgentInstanceService.for_topic` 在里面回落到项目默认 agent，于是每发一条
    消息都有一个 agent handle 拿到 `mentioned=True`。判据得是「对面那一席是不是
    agent」，不是「这是不是私聊」。
    """
    pid = _project(client)
    owner_headers = bearer("user-1")
    join_project_team(client, pid, "alice")
    private = client.get(
        f"/projects/{pid}/private-chat",
        params={"user_handle": "user-1", "peer_handle": "alice"},
        headers=owner_headers,
    ).json()["data"]

    with client.websocket_connect(chat_ws_url(private["id"], "user-1")) as ws:
        ws.send_json({"type": "message", "content": "只说给 alice 听"})
        frames = []
        while True:
            f = ws.receive_json()
            frames.append(f["type"])
            if f["type"] in ("done", "error"):
                break
    assert frames == ["user_block", "done"], f"芝士被叫进了两个人的私聊：{frames}"
