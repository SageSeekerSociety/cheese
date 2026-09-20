"""@ 项目的所有者，他收得到。

这条以前是断的，而且断得没有声音：所有者不写进成员表（「谁是所有者」记在
Project.owner_handle 上），名册以前也不把他补进去，于是 <@他> 解析不到人——既不发
强提醒，也没有任何地方说这条 @ 没送到。个人项目里所有者往往就是说话最多的那个
人，所以这等于「这个项目里 @ 谁都通知不到」。
"""

from tests.integration.conftest import chat_ws_url, session_auth_headers


def _post(client, topic_id: str, content: str, author: str) -> None:
    """发一条人说的话（不唤醒芝士）：@ 的通知在这条消息落库时就发出去了。"""
    with client.websocket_connect(chat_ws_url(topic_id, author)) as ws:
        ws.send_json({"type": "message", "content": content, "summon": False})
        while True:
            if ws.receive_json()["type"] in ("done", "error"):
                break


def _notifs(client, project_id: str, handle: str) -> list[dict]:
    return client.get(
        f"/projects/{project_id}/alerts",
        headers=session_auth_headers(handle),
    ).json()["data"]["data"]


def test_mentioning_the_owner_notifies_them(client):
    project = client.post(
        "/projects", json={"name": "P", "owner_handle": "alice"}
    ).json()["data"]
    topic = client.post(
        "/topics",
        json={"project_id": project["id"], "title": "T", "created_by": "alice"},
    ).json()["data"]
    client.post(
        f"/topics/{topic['id']}/members",
        json={"handle": "bob", "role": "member", "actor": "alice"},
        headers=session_auth_headers("alice"),
    )

    _post(client, topic["id"], "<@alice> 这个你看一下", author="bob")

    alerts = _notifs(client, project["id"], "alice")
    assert len(alerts) == 1
    assert "bob" in alerts[0]["title"]
