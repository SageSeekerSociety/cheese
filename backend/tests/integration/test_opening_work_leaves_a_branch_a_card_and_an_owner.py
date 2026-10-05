"""开一条活留下的是一个分支、一张卡和一个负责人（结论 31）。"""

from tests.conftest import wait_work_idle
from tests.integration.conftest import open_task, post_project, session_auth_headers


def _room(client) -> str:
    project = post_project(client, json={"name": "P"}, owner="alice")
    project_id = project.json()["data"]["id"]
    return client.post(
        "/topics",
        json={"project_id": project_id, "title": "房间"},
        headers=session_auth_headers("alice"),
    ).json()["data"]["id"]


def test_opening_two_pieces_of_work_leaves_two_flat_cards_each_on_its_own_branch(
    client,
):
    """两条活 = 两张卡 + 两条分支 + 两个负责人，而且两条活是平的。

    分支名长什么样不在这里验：平台只记一个名字，建分支的是机器上的执行器。这里验的
    是 P28 验收那句话本身 —— 两条活各有一条自己的分支，名字非空且互不相等，哪天分支
    名改成从别的东西算出来、两条活撞到同一个名字上，这里会红。还有这两条活**从
    哪里开出去**：结论 33 说活在房间里是平的，没有子卡，所以两条都该从同一条基线
    长出来、谁也不挂在谁身上 —— 哪天开活开始默认把新活接在上一条后面，这里会红。
    """
    room_id = _room(client)

    first = open_task(client, room_id, "接口分页")
    second = open_task(client, room_id, "补索引")
    wait_work_idle()

    cards = client.get(f"/topics/{room_id}/tasks").json()["data"]["data"]
    assert [card["id"] for card in cards] == [first["id"], second["id"]]
    assert first["owner_handle"] == second["owner_handle"] == "alice"
    assert first["branch_name"] and second["branch_name"]
    assert first["branch_name"] != second["branch_name"]
    assert first["base_task_id"] is second["base_task_id"] is None
    assert first["base_branch"] == second["base_branch"]
    assert first["base_branch"] not in (first["branch_name"], second["branch_name"])
