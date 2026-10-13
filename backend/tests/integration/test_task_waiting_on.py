"""Who a task waits on is the same on its own page as on the channel's list.

The channel's card says 「待你开始」 to the person a task waits on; the task's
own header read its single row, which did not say who that is, so the same
person saw 「讨论中」 there.
"""

from tests.integration.conftest import post_project, session_auth_headers


def test_a_task_page_says_who_it_waits_on_as_the_channel_list_does(client):
    p = post_project(client, json={"name": "P"}, owner="alice").json()["data"]
    channel = client.post(
        "/topics",
        json={"project_id": p["id"], "title": "预览"},
        headers=session_auth_headers("alice"),
    ).json()["data"]["id"]
    r = client.post(
        f"/topics/{channel}/tasks",
        json={"title": "整理周报"},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200, r.text
    task = r.json()["data"]["id"]

    page = client.get(f"/topics/{task}/task", headers=session_auth_headers("alice"))
    listed = client.get(
        f"/topics/{channel}/tasks", headers=session_auth_headers("alice")
    )

    assert page.status_code == 200, page.text
    row = next(t for t in listed.json()["data"]["data"] if t["id"] == task)
    assert page.json()["data"]["presentation"]["phrase"] == "discussing"
    assert page.json()["data"]["waiting_on"] == "alice"
    assert row["waiting_on"] == page.json()["data"]["waiting_on"]
