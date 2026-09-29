"""命令面板跨项目找话题：列出的是我在侧栏里本来就找得到的那些话题的名字。

人在读代码之前就说得出的几条：我自己的项目、我在名册上的项目里的话题都在；别人的
项目里的话题不在；私聊不在；认不出是谁的时候一个都不给。
"""

from tests.integration.conftest import (
    add_external_member,
    new_project,
    session_auth_headers,
)


def _topic(client, project_id: str, title: str, owner: str) -> str:
    r = client.post(
        "/topics",
        json={"project_id": project_id, "title": title},
        headers=session_auth_headers(owner),
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def _names(client, headers: dict | None = None) -> set[str]:
    r = client.get("/topics/names", headers=headers)
    assert r.status_code == 200, r.text
    return {row["title"] for row in r.json()["data"]["topics"]}


def test_names_come_from_my_projects_only(client):
    mine = new_project(client, name="我的项目", owner="alice")
    rostered = new_project(client, name="我在名册上的", owner="bob")
    theirs = new_project(client, name="别人的", owner="bob")
    add_external_member(client, rostered["id"], "alice", by="bob")
    _topic(client, mine["id"], "登录页改成深色", "alice")
    _topic(client, rostered["id"], "合并队列偶发卡住", "bob")
    _topic(client, theirs["id"], "别人的话题", "bob")

    seen = _names(client, session_auth_headers("alice"))

    assert "登录页改成深色" in seen
    assert "合并队列偶发卡住" in seen, "being on the roster is a claim"
    assert "别人的话题" not in seen, "a stranger's project is not mine to search"


def test_private_chats_are_not_topics(client):
    project = new_project(client, name="私聊所在的项目", owner="alice")
    r = client.get(
        f"/projects/{project['id']}/private-chat",
        params={"user_handle": "alice"},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200, r.text
    chat = r.json()["data"]["id"]

    rows = client.get("/topics/names", headers=session_auth_headers("alice")).json()[
        "data"
    ]["topics"]

    assert chat not in {row["id"] for row in rows}


def test_nobody_gets_nothing(client):
    project = new_project(client, name="有话题的项目", owner="alice")
    _topic(client, project["id"], "谁都不该看到", "alice")
    assert _names(client) == set()
