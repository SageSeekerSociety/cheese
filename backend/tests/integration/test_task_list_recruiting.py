"""The space's task list puts what can still be claimed first.

``lifecycle=recruiting`` is a 题目 nobody has closed, whose registration has
not passed and which has a seat left; ``lifecycle=closed`` is every other one.
The list page shows the first and folds the second away.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from tests.integration.conftest import (
    UserCreator,
    create_approved_space,
    unique_int,
)


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def board(user_client: UserCreator, api_client: TestClient) -> dict:
    creator = user_client.create_user()
    token = user_client.login(api_client, creator.username, creator.password)
    resp = create_approved_space(
        api_client,
        json={
            "name": f"Open first ({unique_int(10000000, 99999999)})",
            "intro": "一块板",
            "description": "能领的排前面",
            "avatarId": 1,
            "taskTemplates": [],
        },
        headers=_auth(token),
    )
    assert resp.status_code == 201, resp.text
    space = resp.json()["data"]["space"]
    return {"token": token, "space_id": space["id"]}


def _task(api_client: TestClient, board: dict, name: str, **more) -> int:
    resp = api_client.post(
        "/tasks",
        headers=_auth(board["token"]),
        json={
            "name": name,
            "intro": name,
            "description": name,
            "space": board["space_id"],
            "submitterType": "USER",
            "resubmittable": True,
            "editable": True,
            "defaultDeadline": 30,
            "deadline": int((time.time() + 7 * 86400) * 1000),
            **more,
        },
    )
    assert resp.status_code == 200, resp.text
    task_id = resp.json()["data"]["task"]["id"]
    resp = api_client.patch(
        f"/tasks/{task_id}",
        headers=_auth(board["token"]),
        json={"approved": "APPROVED"},
    )
    assert resp.status_code == 200, resp.text
    return task_id


def _names(api_client: TestClient, board: dict, lifecycle: str) -> set[str]:
    resp = api_client.get(
        f"/tasks?space={board['space_id']}&approved=APPROVED&lifecycle={lifecycle}",
        headers=_auth(board["token"]),
    )
    assert resp.status_code == 200, resp.text
    return {t["name"] for t in resp.json()["data"]["tasks"]}


def test_what_can_still_be_claimed_is_listed_apart_from_the_rest(
    api_client: TestClient, board: dict, db_session, _portal
):
    _task(api_client, board, "报名中")
    _task(api_client, board, "不限人数", participantLimit=0)
    passed = _task(api_client, board, "报名已截止")
    full = _task(api_client, board, "名额已满", participantLimit=1)
    ended = _task(api_client, board, "已结束")

    async def close_some() -> None:
        from app.domain.task.models import Task, TaskMembership

        (await db_session.get(Task, passed)).deadline = datetime.now(UTC) - timedelta(
            days=1
        )
        (await db_session.get(Task, ended)).ended_at = datetime.now(UTC)
        db_session.add(
            TaskMembership(
                task_id=full,
                member_id=1,
                approved=0,
                is_team=False,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
        )
        await db_session.flush()

    _portal.call(close_some)

    assert _names(api_client, board, "recruiting") == {"报名中", "不限人数"}
    assert _names(api_client, board, "closed") == {"报名已截止", "名额已满", "已结束"}


def _names_for(api_client: TestClient, board: dict, token: str, query: str) -> set[str]:
    resp = api_client.get(
        f"/tasks?space={board['space_id']}&approved=APPROVED&{query}",
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    return {t["name"] for t in resp.json()["data"]["tasks"]}


def test_a_task_is_grouped_by_its_own_state_and_a_claimants_own_are_listed_apart(
    api_client: TestClient,
    user_client: UserCreator,
    board: dict,
    db_session,
    _portal,
):
    """The owner's design (2026-10-26): a task's state is the same for every
    viewer, so the open and closed groups follow the task alone. The tasks a
    viewer holds a claim on are listed apart, in their own group above both
    (``joined=true``), whatever state the task is in; the two groups below
    leave them out (``joined=false``), so none of them is folded away."""
    student = user_client.create_user()
    token = user_client.login(api_client, student.username, student.password)
    joined = api_client.post(
        f"/spaces/{board['space_id']}/members",
        json={"userId": student.user_id},
        headers=_auth(board["token"]),
    )
    assert joined.status_code == 201, joined.text
    after_close = _task(api_client, board, "申请截止后通过", startMode="APPLY")
    mine_over = _task(api_client, board, "还在收申请，我的截止过了", startMode="APPLY")
    _task(api_client, board, "没领的、还开着", startMode="APPLY")

    async def claim() -> None:
        from app.domain.task.models import Task, TaskMembership

        now = datetime.now(UTC)
        (await db_session.get(Task, after_close)).deadline = now - timedelta(days=1)
        for task_id, own in (
            (after_close, now + timedelta(days=7)),
            (mine_over, now - timedelta(days=1)),
        ):
            db_session.add(
                TaskMembership(
                    task_id=task_id,
                    member_id=student.user_id,
                    approved=0,
                    is_team=False,
                    deadline=own,
                    created_at=now,
                    updated_at=now,
                )
            )
        await db_session.flush()

    _portal.call(claim)

    # The task's own state, the same for the claimant and the publisher.
    for viewer in (token, board["token"]):
        assert "申请截止后通过" in _names_for(
            api_client, board, viewer, "lifecycle=closed"
        )
        assert "还在收申请，我的截止过了" in _names_for(
            api_client, board, viewer, "lifecycle=recruiting"
        )
    # The claimant's own group, and the two groups below it without them.
    assert _names_for(api_client, board, token, "joined=true") == {
        "申请截止后通过",
        "还在收申请，我的截止过了",
    }
    assert _names_for(
        api_client, board, token, "joined=false&lifecycle=recruiting"
    ) == {"没领的、还开着"}
    assert (
        _names_for(api_client, board, token, "joined=false&lifecycle=closed") == set()
    )


def _page_of(api_client: TestClient, board: dict, token: str, query: str):
    resp = api_client.get(
        f"/tasks?space={board['space_id']}&approved=APPROVED&queryJoined=true&{query}",
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    return {t["name"]: t for t in data["tasks"]}, data["page"]["total"]


def test_only_live_claims_are_listed_as_the_viewers_own_and_every_task_once(
    api_client: TestClient,
    user_client: UserCreator,
    board: dict,
    db_session,
    _portal,
):
    """The viewer's own group holds their live claims, approved or still
    waiting, and nothing else: a rejected application or a withdrawn claim is
    not theirs, so that task is listed with the others by its own state (the
    review of #4901 at beff8a64 found a rejected one in 我参与的). The three
    reads the list page makes cover every task exactly once."""
    student = user_client.create_user()
    token = user_client.login(api_client, student.username, student.password)
    joined = api_client.post(
        f"/spaces/{board['space_id']}/members",
        json={"userId": student.user_id},
        headers=_auth(board["token"]),
    )
    assert joined.status_code == 201, joined.text
    states = {"通过了": 0, "在等通过": 2, "被拒了": 1, "退出了": 0}
    ids = {name: _task(api_client, board, name, startMode="APPLY") for name in states}
    _task(api_client, board, "没申请过", startMode="APPLY")

    async def claim() -> None:
        from app.domain.task.models import TaskMembership

        now = datetime.now(UTC)
        for name, approved in states.items():
            db_session.add(
                TaskMembership(
                    task_id=ids[name],
                    member_id=student.user_id,
                    approved=approved,
                    is_team=False,
                    deadline=now + timedelta(days=7),
                    created_at=now,
                    updated_at=now,
                    deleted_at=now if name == "退出了" else None,
                )
            )
        await db_session.flush()

    _portal.call(claim)

    mine, mine_total = _page_of(api_client, board, token, "joined=true")
    assert set(mine) == {"通过了", "在等通过"}
    assert mine_total == 2
    # The rows of the own group say the same: every one of them is joined.
    assert all(row["joined"] for row in mine.values())
    rest, rest_total = _page_of(
        api_client, board, token, "joined=false&lifecycle=recruiting"
    )
    closed, closed_total = _page_of(
        api_client, board, token, "joined=false&lifecycle=closed"
    )
    assert {"被拒了", "退出了", "没申请过"} <= set(rest)
    assert not any(row["joined"] for row in [*rest.values(), *closed.values()])
    everything, total = _page_of(api_client, board, token, "")
    assert mine_total + rest_total + closed_total == total
    assert set(mine) | set(rest) | set(closed) == set(everything)
    assert not (set(mine) & set(rest)) and not (set(rest) & set(closed))
