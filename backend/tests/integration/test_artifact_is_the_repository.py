"""交出去一次合并的项目，清单上只有一项：它的仓库 (#1085)。

这个文件是一次真实事故的回归。那天一个项目并行推着五条功能分支，两张验收卡隔 74
秒先后递上来，各自判断「这次交付的是一样新东西」—— 每一个判断在它自己的视野里都
是对的（一条分支只看得见自己），于是清单上长出两项、各自第 0 版，而这个项目交出去
的其实只有一样东西：它的仓库。

所以这里问的不是「agent 判断得对不对」，是**这个判断还在不在**：交出去一次合并的
那条路上，没有谁可以声明产物，平台自己认得出是清单上哪一项。并行多少条分支、隔多
久递卡，都不改变这个答案。
"""

import pytest

from tests.delivery import delivery_headers, delivery_task
from tests.integration.conftest import session_auth_headers
from tests.integration.test_accept_pr import (
    _give_card_a_pr,
    _rendered_head,
    app_world,  # noqa: F401
)


@pytest.fixture(autouse=True)
def remote_delivery(client, request):
    client.artifact_forge = request.getfixturevalue("app_world")


def _project(client, name: str = "知是平台") -> str:
    r = client.post("/projects", json={"name": name})
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def _room(client, project_id: str, title: str = "做一个东西") -> str:
    r = client.post("/topics", json={"project_id": project_id, "title": title})
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def _merge_card(client, room_id: str, *, again=False, **body):
    """递一张交出去这次合并本身的卡 —— `deliver` / `deliver_url` 都不给。"""
    task = delivery_task(client, room_id, new=again)
    response = client.post(
        f"/topics/{room_id}/tasks/{task.id}/accept-card",
        headers=delivery_headers(client, room_id),
        json={
            "change_subject": "feat(space): members and invite codes",
            "reviewer_handle": "alice",
            **body,
        },
    )
    if response.status_code == 200:
        world = client.artifact_forge
        head = _give_card_a_pr(
            client,
            world,
            room_id,
            response.json()["data"]["id"],
            number=100 + len(world["fake"].prs),
        )
        world["fake"].check_state_by_sha[head] = ("success", "All checks passed")
    return response


def _manifest(client, project_id: str) -> list[tuple[str, int]]:
    r = client.get(f"/projects/{project_id}/artifacts")
    assert r.status_code == 200, r.text
    return [(a["name"], a["version"]) for a in r.json()["data"]["data"]]


def _accept(client, card_id: str):
    return client.post(
        f"/accept-cards/{card_id}/accept",
        json={"decided_by": "alice", "head_sha": _rendered_head(client, card_id)},
        headers=session_auth_headers("alice"),
    )


def test_a_merge_needs_no_declaration_and_still_lands_on_the_list(client):
    pid = _project(client)
    rid = _room(client, pid)

    r = _merge_card(client, rid)

    assert r.status_code == 200, r.text
    assert r.json()["data"]["artifact"]["name"] == "知是平台"
    assert _manifest(client, pid) == [("知是平台", 0)]


def test_parallel_branches_deliver_one_artifact(client):
    """事故本身：两个房间各推一条分支，各自递卡，清单上仍然只有一项。"""
    pid = _project(client)
    space = _room(client, pid, "空间成员与邀请码")
    shell = _room(client, pid, "壳声明与入口渲染")

    first = _merge_card(client, space)
    second = _merge_card(client, shell)

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert (
        first.json()["data"]["artifact"]["id"]
        == second.json()["data"]["artifact"]["id"]
    )
    assert _manifest(client, pid) == [("知是平台", 0)]


def test_two_merges_are_two_versions_of_the_one_artifact(client):
    pid = _project(client)
    rid = _room(client, pid)
    first = _merge_card(client, rid).json()["data"]["id"]
    assert _accept(client, first).status_code == 200

    second = _merge_card(client, rid, again=True).json()["data"]["id"]
    assert _accept(client, second).status_code == 200

    assert _manifest(client, pid) == [("知是平台", 2)]


@pytest.mark.parametrize(
    "declared",
    [
        {"new_artifact": "壳（Shell）声明与入口渲染"},
        {"artifact": "00000000-0000-0000-0000-000000000000"},
        {"about": "这个项目的后端服务"},
    ],
)
def test_declaring_an_artifact_on_a_merge_is_refused(client, declared):
    """打回而不是默默忽略：收下却不起作用的参数，读起来跟起了作用一模一样。"""
    pid = _project(client)
    rid = _room(client, pid)

    r = _merge_card(client, rid, **declared)

    assert r.status_code == 422
    assert "仓库" in r.json()["message"]
    assert _manifest(client, pid) == []


def test_renaming_the_repository_item_keeps_the_next_merge_on_it(client):
    """认的是标记不是名字 —— 否则人一改名，下一次合并就再长出一行。"""
    pid = _project(client)
    rid = _room(client, pid)
    cid = _merge_card(client, rid).json()["data"]["id"]
    assert _accept(client, cid).status_code == 200
    aid = client.get(f"/projects/{pid}/artifacts").json()["data"]["data"][0]["id"]
    assert (
        client.patch(
            f"/projects/{pid}/artifacts/{aid}", json={"name": "知是平台后端"}
        ).status_code
        == 200
    )

    again = _merge_card(client, rid, again=True)

    assert again.status_code == 200, again.text
    assert again.json()["data"]["artifact"]["id"] == aid
    assert _manifest(client, pid) == [("知是平台后端", 1)]
