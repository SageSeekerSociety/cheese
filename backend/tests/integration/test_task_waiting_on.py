"""Who a task waits on is the same on its own page as on the channel's list.

The channel's card says 「待 某某 审阅」 for the person a delivery waits on; the
task's own header read its single row, which did not say who that is, so the
same person saw a different line there.
"""

import uuid

from app.domain.review.models import AcceptCard, AcceptStatus
from tests.integration.conftest import (
    join_project_team,
    open_task,
    post_project,
    session_auth_headers,
)


def test_a_task_page_says_who_it_waits_on_as_the_channel_list_does(client):
    p = post_project(client, json={"name": "P"}, owner="alice").json()["data"]
    join_project_team(client, p["id"], "bob")
    channel = client.post(
        "/topics",
        json={"project_id": p["id"], "title": "预览"},
        headers=session_auth_headers("alice"),
    ).json()["data"]["id"]
    task = open_task(client, channel, "整理周报", owner="alice")["id"]

    async def hand_in():
        async with client.test_factory() as session:
            session.add(
                AcceptCard(
                    topic_id=uuid.UUID(channel),
                    task_id=uuid.UUID(task),
                    reviewer_handle="bob",
                    status=AcceptStatus.pending,
                )
            )
            await session.commit()

    client.portal.call(hand_in)

    page = client.get(f"/topics/{task}/task", headers=session_auth_headers("alice"))
    listed = client.get(
        f"/topics/{channel}/tasks", headers=session_auth_headers("alice")
    )

    assert page.status_code == 200, page.text
    row = next(t for t in listed.json()["data"]["data"] if t["id"] == task)
    assert page.json()["data"]["waiting_on"] == "bob"
    assert row["waiting_on"] == page.json()["data"]["waiting_on"]
