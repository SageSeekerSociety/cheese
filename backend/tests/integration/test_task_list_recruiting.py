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
