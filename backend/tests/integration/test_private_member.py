"""私聊 (spec §1) + 成员页 (spec §7.2)."""

from app.domain.identity.handles import topic_agent_handle
from tests.integration.conftest import chat_ws_url, session_auth_headers


def _project(client) -> str:
    return client.post(
        "/projects", json={"name": "P", "owner_handle": "user-1"}
    ).json()["data"]["id"]


def test_private_chat_get_or_create_and_hidden_from_tree(client):
    pid = _project(client)

    r1 = client.get(f"/projects/{pid}/private-chat?user_handle=user-1")
    assert r1.status_code == 200
    private = r1.json()["data"]
    # Idempotent: same private topic returned.
    r2 = client.get(f"/projects/{pid}/private-chat?user_handle=user-1")
    assert r2.json()["data"]["id"] == private["id"]

    members = client.get(
        f"/topics/{private['id']}/members",
        headers=session_auth_headers("user-1"),
    ).json()["data"]["data"]
    assert {m["member_handle"] for m in members} == {
        "user-1",
        topic_agent_handle(private["id"]),
    }

    # Private chat is NOT part of the topic tree.
    tree = client.get(f"/topics?project_id={pid}").json()["data"]["data"]
    assert all(t["id"] != private["id"] for t in tree)

    # It still works as a chat (stub agent replies when summoned).
    with client.websocket_connect(chat_ws_url(private["id"], "user-1")) as ws:
        ws.send_json({"type": "message", "content": "设个偏好", "summon": True})
        frames = []
        while True:
            f = ws.receive_json()
            frames.append(f["type"])
            if f["type"] in ("done", "error"):
                break
    assert "assistant_block" in frames


def test_private_human_chat_seeds_both_participants_and_rejects_outsiders(
    client, bearer
):
    pid = _project(client)
    owner_headers = bearer("user-1")
    client.post(
        f"/projects/{pid}/members",
        json={"user_handle": "bob"},
        headers=owner_headers,
    )
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
        f"/projects/{pid}/members",
        json={"user_handle": "user-1"},
        headers=bearer("user-1"),  # the project owner
    )
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
    assert s["role"] == "member"
    assert any(t["title"] == "我开的话题" for t in s["topics_started"])
    assert any(w["title"] == "等你定" for w in s["waiting_on_you"])
