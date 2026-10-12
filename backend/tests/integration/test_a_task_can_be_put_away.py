"""一条活可以由它的负责人收起来（归档），也可以放回来。

归档先把任务关掉、再记一个时刻，所以「已归档」一定是「已关闭」的一种，各处
「还开着就怎样」的判断照旧成立；要问的只有清单——收起来的活不在任何一份清单
里，除了它自己那份「已归档」。放回来只抹掉那个时刻，不替它重新打开。
"""

import uuid

from tests.integration.conftest import (
    join_project_team,
    open_task,
    post_project,
    session_auth_headers,
)

ALICE = session_auth_headers("alice")


def _project(client) -> tuple[str, str, str]:
    pid = post_project(client, json={"name": "P"}, owner="alice").json()["data"]["id"]
    join_project_team(client, pid, "bob")
    rooms = [
        client.post(
            "/topics", json={"project_id": pid, "title": title}, headers=ALICE
        ).json()["data"]["id"]
        for title in ("前端", "后端")
    ]
    return pid, rooms[0], rooms[1]


def _archive(client, task: dict, handle: str = "alice"):
    return client.post(
        f"/topics/{task['id']}/task/archive", headers=session_auth_headers(handle)
    )


def _unarchive(client, task: dict, handle: str = "alice"):
    return client.post(
        f"/topics/{task['id']}/task/unarchive", headers=session_auth_headers(handle)
    )


def _one(client, task: dict, handle: str = "alice") -> dict:
    r = client.get(f"/topics/{task['id']}/task", headers=session_auth_headers(handle))
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _read(client, pid: str, **params) -> dict:
    r = client.get(f"/projects/{pid}/tasks", params=params, headers=ALICE)
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _titles(rows: list[dict]) -> set[str]:
    return {row["title"] for row in rows}


def _room_titles(client, room_id: str) -> set[str]:
    r = client.get(f"/topics/{room_id}/tasks", headers=ALICE)
    assert r.status_code == 200, r.text
    return _titles(r.json()["data"]["data"])


def test_the_owner_puts_a_task_away_and_it_closes_first(client):
    _pid, room, _back = _project(client)
    task = open_task(client, room, "收起来", owner="alice")

    r = _archive(client, task)

    assert r.status_code == 200, r.text
    out = r.json()["data"]
    assert out["archived_at"] is not None
    # 归档＝先关掉：它不再是「还开着」，所以各处「还开着就别开工」的判断都还成立。
    assert out["status"] == "closed"
    # 卡片上写「已归档」，不写「已关闭」——收走的和干完的是两件事。
    assert out["presentation"] == {"column": "archived", "phrase": "archived"}


def test_only_the_owner_puts_a_task_away(client):
    _pid, room, _back = _project(client)
    task = open_task(client, room, "别人的活", owner="alice")

    assert _archive(client, task, handle="bob").status_code == 403

    assert _one(client, task)["archived_at"] is None


def test_a_put_away_task_leaves_every_list_but_its_own(client):
    pid, room, back = _project(client)
    kept = open_task(client, room, "留着的", owner="alice", start=False)
    put_away = open_task(client, room, "收起来的", owner="alice", start=False)
    closed = open_task(client, back, "干完的", owner="alice", start=False)
    r = client.post(f"/topics/{closed['id']}/close", json={}, headers=ALICE)
    assert r.status_code == 200, r.text

    assert _archive(client, put_away).status_code == 200

    assert _room_titles(client, room) == {"留着的"}
    assert _titles(_read(pid)["data"]) == {"留着的", "干完的"}
    assert _titles(_read(pid, status="open")["data"]) == {"留着的"}
    assert _titles(_read(pid, status="closed")["data"]) == {"干完的"}
    assert _titles(_read(pid, status="archived")["data"]) == {"收起来的"}
    # 分页读那一支的计数和它列出来的一致。
    assert _read(pid, status="archived", limit=10)["counts"]["all"] == 1
    assert _one(client, kept)["title"] == "留着的"


def test_a_message_that_names_a_put_away_task_still_finds_it(client):
    """收起来的是清单里的那一行，不是这条活：有人点名要它，照旧给。"""
    _pid, room, _back = _project(client)
    task = open_task(client, room, "收起来的", owner="alice", start=False)
    assert _archive(client, task).status_code == 200

    r = client.get(f"/topics/{room}/tasks", params={"ids": [task["id"]]}, headers=ALICE)

    assert r.status_code == 200, r.text
    assert _titles(r.json()["data"]["data"]) == {"收起来的"}


def test_unarchiving_puts_it_back_still_closed(client):
    pid, room, _back = _project(client)
    task = open_task(client, room, "收起来的", owner="alice", start=False)
    assert _archive(client, task).status_code == 200

    r = _unarchive(client, task)

    assert r.status_code == 200, r.text
    out = r.json()["data"]
    assert out["archived_at"] is None
    # 放回来不等于接着做：它还是关着的，要接着做是「重新打开」那一件事。
    assert out["status"] == "closed"
    assert _titles(_read(pid, status="closed")["data"]) == {"收起来的"}
    assert _titles(_read(pid, status="archived")["data"]) == set()
    assert _room_titles(client, room) == set()


def test_only_the_owner_brings_a_task_back(client):
    _pid, room, _back = _project(client)
    task = open_task(client, room, "收起来的", owner="alice", start=False)
    assert _archive(client, task).status_code == 200

    assert _unarchive(client, task, handle="bob").status_code == 403

    assert _one(client, task)["archived_at"] is not None


def test_reopening_clears_the_put_away_moment(client):
    """重开一条活就是接着做，它当然不再是从清单里收走的那一条。"""
    _pid, room, _back = _project(client)
    task = open_task(client, room, "收起来的", owner="alice")
    assert _archive(client, task).status_code == 200

    r = client.post(f"/topics/{task['id']}/reopen", headers=ALICE)

    assert r.status_code == 200, r.text
    out = r.json()["data"]
    assert out["archived_at"] is None
    assert out["status"] == "open"


def test_a_put_away_task_takes_no_more_messages(client):
    _pid, room, _back = _project(client)
    task = open_task(client, room, "收起来的", owner="alice")
    assert _archive(client, task).status_code == 200

    r = client.post(
        f"/topics/{task['id']}/messages",
        json={"content": "还在吗", "request_id": str(uuid.uuid4())},
        headers=ALICE,
    )

    assert r.status_code == 422
