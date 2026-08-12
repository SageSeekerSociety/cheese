"""@all / @here notify the whole topic roster (群播, fusion-design §3)."""

from tests.integration.conftest import chat_ws_url, session_auth_headers


def _project_topic(client, created_by: str = "alice") -> tuple[str, str]:
    p = client.post("/api/projects", json={"name": "P"}).json()["data"]
    t = client.post(
        "/api/topics",
        json={"project_id": p["id"], "title": "T", "created_by": created_by},
    ).json()["data"]
    return p["id"], t["id"]


def _add(client, tid: str, handle: str, actor: str = "alice") -> None:
    r = client.post(
        f"/api/topics/{tid}/members",
        json={"handle": handle, "role": "member", "actor": actor},
    )
    assert r.status_code == 200


def _post(ws, content: str) -> None:
    # human-only post (summon False): the @all notifications fire on the human
    # block persist, before any agent turn. The sender is the socket's token,
    # not a body field.
    ws.send_json({"type": "message", "content": content, "summon": False})
    while True:
        f = ws.receive_json()
        if f["type"] in ("done", "error"):
            break


def _notifs(client, pid: str, handle: str) -> list[dict]:
    """What ``handle`` finds in their own mailbox — read as them, since a
    mailbox is served to its owner, not to whoever names it in the query."""
    return client.get(
        f"/api/projects/{pid}/notifications", headers=session_auth_headers(handle)
    ).json()["data"]["data"]


def test_at_all_notifies_every_member_except_sender_and_cheese(client):
    pid, tid = _project_topic(client, created_by="alice")
    _add(client, tid, "bob")
    _add(client, tid, "carol")
    with client.websocket_connect(chat_ws_url(tid, "alice")) as ws:
        _post(ws, "<@all> 大家看一下")

    assert len(_notifs(client, pid, "bob")) == 1
    assert len(_notifs(client, pid, "carol")) == 1
    # sender is not notified of their own broadcast
    assert _notifs(client, pid, "alice") == []
    # 芝士 is a member but the agent is summoned separately, never @-notified
    assert _notifs(client, pid, "cheese") == []


def test_at_here_equals_all_for_now(client):
    pid, tid = _project_topic(client, created_by="alice")
    _add(client, tid, "bob")
    with client.websocket_connect(chat_ws_url(tid, "alice")) as ws:
        _post(ws, "<@here> 在的人")
    assert len(_notifs(client, pid, "bob")) == 1
