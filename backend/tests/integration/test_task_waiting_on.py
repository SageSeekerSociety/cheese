"""Who a task waits on is the same on its own page as on the channel's list.

The channel's card says 「待你开始」 to the person a task waits on; the task's
own header read its single row, which did not say who that is, so the same
person saw 「讨论中」 there.

人建的活建出来即已开始（#718），所以等着的活只剩芝士自己想的那些
（`teammate_task`），而这一页说的仍然是「等谁」。
"""

from tests.conftest import wait_work_idle
from tests.integration.conftest import post_project, session_auth_headers, teammate_task


def test_a_task_page_says_who_it_waits_on_as_the_channel_list_does(client):
    p = post_project(client, json={"name": "P"}, owner="alice").json()["data"]
    channel = client.post(
        "/topics",
        json={"project_id": p["id"], "title": "预览"},
        headers=session_auth_headers("alice"),
    ).json()["data"]["id"]
    task = teammate_task(client, channel, "整理周报", owner="alice")["id"]
    # 等芝士把初稿写完 —— 还在跑的那一会儿这一格不是「待开始」（`owner_acts_on`）。
    wait_work_idle()

    page = client.get(f"/topics/{task}/task", headers=session_auth_headers("alice"))
    listed = client.get(
        f"/topics/{channel}/tasks", headers=session_auth_headers("alice")
    )

    assert page.status_code == 200, page.text
    row = next(t for t in listed.json()["data"]["data"] if t["id"] == task)
    assert page.json()["data"]["presentation"]["phrase"] == "discussing"
    assert page.json()["data"]["waiting_on"] == "alice"
    assert row["waiting_on"] == page.json()["data"]["waiting_on"]
