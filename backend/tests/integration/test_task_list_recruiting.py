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


def _names_for(api_client: TestClient, board: dict, token: str, lifecycle: str):
    resp = api_client.get(
        f"/tasks?space={board['space_id']}&approved=APPROVED&lifecycle={lifecycle}",
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    return {t["name"] for t in resp.json()["data"]["tasks"]}


def test_a_task_still_open_to_its_approved_claimant_is_not_folded_away_for_them(
    api_client: TestClient,
    user_client: UserCreator,
    board: dict,
    db_session,
    _portal,
):
    """On a task applied for, 申请截止 closes applying only: someone approved
    whose own deadline is still ahead can start or keep working, so for them
    the task is listed with what is open; for everyone else it is closed. On a
    task started directly, its deadline is everyone's."""
    student = user_client.create_user()
    token = user_client.login(api_client, student.username, student.password)
    joined = api_client.post(
        f"/spaces/{board['space_id']}/members",
        json={"userId": student.user_id},
        headers=_auth(board["token"]),
    )
    assert joined.status_code == 201, joined.text
    mine = _task(api_client, board, "申请截止后通过", startMode="APPLY")
    mine_over = _task(api_client, board, "自己的截止也过了", startMode="APPLY")
    direct = _task(api_client, board, "直接开始已截止", startMode="DIRECT")

    async def close_and_approve() -> None:
        from app.domain.task.models import Task, TaskMembership

        now = datetime.now(UTC)
        for task_id, own in (
            (mine, now + timedelta(days=7)),
            (mine_over, now - timedelta(days=1)),
            (direct, now + timedelta(days=7)),
        ):
            (await db_session.get(Task, task_id)).deadline = now - timedelta(days=1)
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

    _portal.call(close_and_approve)

    assert _names_for(api_client, board, token, "recruiting") == {"申请截止后通过"}
    assert _names_for(api_client, board, token, "closed") == {
        "自己的截止也过了",
        "直接开始已截止",
    }
    # Nobody else holds it open: for the publisher it is closed.
    assert "申请截止后通过" in _names(api_client, board, "closed")


def test_a_task_its_approved_claimant_can_no_longer_act_on_is_closed_for_them(
    api_client: TestClient,
    user_client: UserCreator,
    board: dict,
    db_session,
    _portal,
):
    """The mirror case: the task still takes applications, but the
    claimant's own deadline has passed, so they can neither start nor hand
    in. For them it is closed; for everyone else it is still open."""
    student = user_client.create_user()
    token = user_client.login(api_client, student.username, student.password)
    joined = api_client.post(
        f"/spaces/{board['space_id']}/members",
        json={"userId": student.user_id},
        headers=_auth(board["token"]),
    )
    assert joined.status_code == 201, joined.text
    task_id = _task(api_client, board, "还在收申请，我的截止过了", startMode="APPLY")

    async def approve_with_a_passed_deadline() -> None:
        from app.domain.task.models import TaskMembership

        now = datetime.now(UTC)
        db_session.add(
            TaskMembership(
                task_id=task_id,
                member_id=student.user_id,
                approved=0,
                is_team=False,
                deadline=now - timedelta(days=1),
                created_at=now,
                updated_at=now,
            )
        )
        await db_session.flush()

    _portal.call(approve_with_a_passed_deadline)

    assert _names_for(api_client, board, token, "closed") == {
        "还在收申请，我的截止过了"
    }
    assert _names_for(api_client, board, token, "recruiting") == set()
    assert "还在收申请，我的截止过了" in _names(api_client, board, "recruiting")
