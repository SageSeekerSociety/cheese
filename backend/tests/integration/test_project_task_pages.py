"""The project's closed tasks are read a page at a time.

Closed tasks only grow, so the all-tasks page scrolls through them: the most
recently moved first, a page at a time, narrowed to a channel and to whose they
are to the reader, with the counts for the filters it shows. Without `limit`
the list answers whole, as before.
"""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import update

from app.domain.room_task.models import Task
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
        "mine-front": open_task(client, front, "我的前端", owner="alice"),
        "mine-back": open_task(client, back, "我的后端", owner="alice"),
        "helping": open_task(
            client, front, "帮忙的", owner="bob", contributors=["alice"]
        ),
        "others": open_task(client, back, "别人的", owner="bob"),
    }
    # Closing writes into the task's conversation: the last one closed moved last.
    for key in ("mine-front", "helping", "others", "mine-back"):
        _close(client, tasks[key], "bob" if key in ("helping", "others") else "alice")
    still_open = open_task(client, front, "还在做", owner="alice")
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


def _approved_deliveries(client, tasks: list[dict]) -> None:
    """Existing approvals: a read test needs neither delivery files nor a forge."""

    async def seed() -> None:
        async with client.test_request_factory() as session:
            await session.execute(
                update(Task)
                .where(Task.id.in_([uuid.UUID(t["id"]) for t in tasks]))
                .values(accepted_at=datetime.now(UTC), accepted_by="alice")
            )
            await session.commit()

    client.portal.call(seed)


@pytest.fixture
def delivery_tasks(client):
    pid, front, back = _project(client)
    by_status = {}
    for status in ("open", "closed"):
        tasks = {
            "mine-front": open_task(client, front, "采纳的我的活", owner="alice"),
            "mine-back": open_task(client, back, "未采纳的我的活", owner="alice"),
            "helping": open_task(
                client, front, "采纳的帮忙活", owner="bob", contributors=["alice"]
            ),
            "others-back": open_task(client, back, "采纳的别人的活", owner="bob"),
            "others-front": open_task(client, front, "未采纳的别人的活", owner="bob"),
        }
        _approved_deliveries(
            client, [tasks[k] for k in ("mine-front", "helping", "others-back")]
        )
        if status == "closed":
            for key, task in tasks.items():
                _close(client, task, "alice" if key.startswith("mine") else "bob")
        by_status[status] = tasks
    return pid, front, back, by_status


@pytest.mark.parametrize("status", ["open", "closed"])
@pytest.mark.parametrize("accepted", [True, False, None])
def test_approval_filters_before_paging_and_counts_with_channel_and_whose(
    client, delivery_tasks, status, accepted
):
    pid, front, back, by_status = delivery_tasks
    tasks = by_status[status]
    approved = {"mine-front", "helping", "others-back"}
    keys = {key for key in tasks if accepted is None or (key in approved) == accepted}
    params = {"status": status, "limit": 1}
    if accepted is not None:
        params["accepted"] = accepted
    counts = {
        True: {"all": 3, "mine": 1, "helping": 1, "others": 1},
        False: {"all": 2, "mine": 1, "helping": 0, "others": 1},
        None: {"all": 5, "mine": 2, "helping": 1, "others": 2},
    }[accepted]
    seen = []
    payload = _read(client, pid, **params)
    while True:
        # A filtered page is full until its final page; counts are not page-local.
        assert len(payload["data"]) == 1
        assert payload["counts"] == counts
        seen += [t["id"] for t in payload["data"]]
        if not payload["has_more"]:
            assert payload["next"] is None
            break
        assert len(seen) < len(keys)
        payload = _read(client, pid, **params, before=payload["next"])
    assert len(seen) == len(set(seen)) == len(keys)
    assert set(seen) == {tasks[key]["id"] for key in keys}

    for room, room_keys in (
        (front, {"mine-front", "helping", "others-front"}),
        (back, {"mine-back", "others-back"}),
    ):
        in_room = keys & room_keys
        room_counts = {
            "all": len(in_room),
            "mine": len(in_room & {"mine-front", "mine-back"}),
            "helping": len(in_room & {"helping"}),
            "others": len(in_room & {"others-front", "others-back"}),
        }
        for whose, whose_keys in (
            ("mine", {"mine-front", "mine-back"}),
            ("helping", {"helping"}),
            ("others", {"others-front", "others-back"}),
        ):
            narrowed = _read(
                client, pid, **{**params, "limit": 10}, channel=room, whose=whose
            )
            assert {t["id"] for t in narrowed["data"]} == {
                tasks[key]["id"] for key in in_room & whose_keys
            }
            # Counts keep all whose buckets, but share channel/approval/status.
            assert narrowed["counts"] == room_counts
            assert not narrowed["has_more"]
            assert narrowed["next"] is None


def test_whole_lists_apply_approval_independently_of_open_or_closed(
    client, delivery_tasks
):
    pid, _front, _back, by_status = delivery_tasks
    approved = {"mine-front", "helping", "others-back"}
    for status in (None, "open", "closed"):
        for accepted in (None, True, False):
            params = {}
            if status is not None:
                params["status"] = status
            if accepted is not None:
                params["accepted"] = accepted
            payload = _read(client, pid, **params)
            assert {t["id"] for t in payload["data"]} == {
                task["id"]
                for state, tasks in by_status.items()
                for key, task in tasks.items()
                if (status is None or state == status)
                and (accepted is None or (key in approved) == accepted)
            }
            assert "has_more" not in payload


def test_approval_filter_does_not_expose_private_channel_tasks_or_counts(client):
    pid, front, _back = _project(client)
    response = client.post(
        "/topics",
        json={"project_id": pid, "title": "私密", "members_only": True},
        headers=session_auth_headers("bob"),
    )
    assert response.status_code == 200, response.text
    private = response.json()["data"]["id"]
    visible = open_task(client, front, "能读的活", owner="alice")
    hidden = open_task(client, private, "不能读的活", owner="bob")
    _approved_deliveries(client, [visible, hidden])
    for task, owner in ((visible, "alice"), (hidden, "bob")):
        _close(client, task, owner)

    whole = _read(client, pid, accepted=True)
    paged = _read(client, pid, status="closed", accepted=True, limit=1)
    assert [t["id"] for t in whole["data"]] == [visible["id"]]
    assert [t["id"] for t in paged["data"]] == [visible["id"]]
    assert paged["counts"] == {"all": 1, "mine": 1, "helping": 0, "others": 0}
    assert not paged["has_more"]
    hidden_page = _read(
        client, pid, status="closed", accepted=True, channel=private, limit=1
    )
    assert hidden_page["data"] == []
    assert hidden_page["counts"] == {"all": 0, "mine": 0, "helping": 0, "others": 0}
