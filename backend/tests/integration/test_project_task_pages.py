"""The project's closed tasks are read a page at a time.

Closed tasks only grow, so the all-tasks page scrolls through them: the most
recently moved first, a page at a time, narrowed to a channel and to whose they
are to the reader, with the counts for the filters it shows. Without `limit`
the list answers whole, as before.
"""

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


def _close(client, task: dict, owner: str) -> None:
    r = client.post(
        f"/topics/{task['id']}/close",
        json={"conclusion": "好了"},
        headers=session_auth_headers(owner),
    )
    assert r.status_code == 200, r.text


def _read(client, pid: str, **params) -> dict:
    r = client.get(f"/projects/{pid}/tasks", params=params, headers=ALICE)
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _closed_tasks(client):
    pid, front, back = _project(client)
    tasks = {
        "mine-front": open_task(client, front, "我的前端", owner="alice", start=False),
        "mine-back": open_task(client, back, "我的后端", owner="alice", start=False),
        "helping": open_task(
            client, front, "帮忙的", owner="bob", start=False, contributors=["alice"]
        ),
        "others": open_task(client, back, "别人的", owner="bob", start=False),
    }
    # Closing writes into the task's conversation: the last one closed moved last.
    for key in ("mine-front", "helping", "others", "mine-back"):
        _close(client, tasks[key], "bob" if key in ("helping", "others") else "alice")
    still_open = open_task(client, front, "还在做", owner="alice", start=False)
    return pid, front, tasks, still_open


def test_paging_through_closed_tasks_sees_each_once_most_recently_moved_first(client):
    pid, _front, tasks, still_open = _closed_tasks(client)

    seen: list[str] = []
    payload = _read(client, pid, status="closed", limit=3)
    seen += [t["id"] for t in payload["data"]]
    while payload["has_more"]:
        payload = _read(client, pid, status="closed", limit=3, before=payload["next"])
        seen += [t["id"] for t in payload["data"]]

    assert sorted(seen) == sorted(t["id"] for t in tasks.values())
    assert seen[0] == tasks["mine-back"]["id"]
    assert still_open["id"] not in seen
    assert payload["counts"] == {"all": 4, "mine": 2, "helping": 1, "others": 1}


def test_a_page_narrows_to_a_channel_and_to_whose_the_tasks_are(client):
    pid, front, tasks, _still_open = _closed_tasks(client)

    in_front = _read(client, pid, status="closed", limit=10, channel=front)
    helping = _read(client, pid, status="closed", limit=10, whose="helping")
    others = _read(client, pid, status="closed", limit=10, whose="others")

    assert {t["id"] for t in in_front["data"]} == {
        tasks["mine-front"]["id"],
        tasks["helping"]["id"],
    }
    assert in_front["counts"] == {"all": 2, "mine": 1, "helping": 1, "others": 0}
    assert [t["id"] for t in helping["data"]] == [tasks["helping"]["id"]]
    assert [t["id"] for t in others["data"]] == [tasks["others"]["id"]]


def test_a_page_is_of_one_status_and_the_whole_list_is_unchanged(client):
    pid, _front, tasks, still_open = _closed_tasks(client)

    r = client.get(f"/projects/{pid}/tasks", params={"limit": 5}, headers=ALICE)
    whole = _read(client, pid)

    assert r.status_code == 422, r.text
    assert len(whole["data"]) == 5
    assert "has_more" not in whole
