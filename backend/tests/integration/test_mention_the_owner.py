"""@ 项目的所有者，他收得到。

这条以前是断的，而且断得没有声音：所有者不写进成员表（「谁是所有者」记在
Project.owner_handle 上），名册以前也不把他补进去，于是 <@他> 解析不到人——既不发
强提醒，也没有任何地方说这条 @ 没送到。个人项目里所有者往往就是说话最多的那个
人，所以这等于「这个项目里 @ 谁都通知不到」。
"""

from tests.conftest import seed_user
from tests.integration.conftest import (
    inbox,
    join_project_team,
    post_message,
    post_project,
    room_socket,
    session_auth_headers,
)


def _post(client, topic_id: str, content: str, author: str) -> None:
    """发一条人说的话（不唤醒芝士）：@ 的通知在这条消息落库时就发出去了。"""
    with room_socket(client, topic_id, author) as ws:
        post_message(client, topic_id, author, {"content": content})
        while True:
            if ws.receive_json()["type"] in ("done", "error"):
                break


def test_mentioning_the_owner_notifies_them(client):
    seed_user(client, "alice")
    project = post_project(client, json={"name": "P"}, owner="alice").json()["data"]
    topic = client.post(
        "/topics",
        json={"project_id": project["id"], "title": "T"},
        headers=session_auth_headers("alice"),
    ).json()["data"]
    join_project_team(client, project["id"], "bob")
    client.post(
        f"/topics/{topic['id']}/members",
        json={"handle": "bob", "role": "member", "actor": "alice"},
        headers=session_auth_headers("alice"),
    )

    _post(client, topic["id"], "<@alice> 这个你看一下", author="bob")

    [mention] = inbox(client, "alice", "MENTION")
    assert mention["contextMetadata"]["author"] == "bob"
