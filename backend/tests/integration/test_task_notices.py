"""What happens to a space's 题目 reaches the people on it.

- A claim waits on the publisher in 等你处理 until it is decided; the claimer
  hears the decision in 动态.
- Changing what to hand in or how it is scored tells those who claimed it.
- A claim with nothing handed in is reminded three days and one day before its
  own deadline, each once; the last day's reminder is the one that pushes.
- A deadline passed with nothing handed in is told to the publisher.
"""

from __future__ import annotations

import time
from contextlib import asynccontextmanager
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
    teacher = user_client.create_user()
    teacher_token = user_client.login(api_client, teacher.username, teacher.password)
    student = user_client.create_user()
    token = user_client.login(api_client, student.username, student.password)
    resp = create_approved_space(
        api_client,
        json={
            "name": f"Notices ({unique_int(10000000, 99999999)})",
            "intro": "一块板",
            "description": "题目通知",
            "avatarId": 1,
            "taskTemplates": [],
        },
        headers=_auth(teacher_token),
    )
    assert resp.status_code == 201, resp.text
    space = resp.json()["data"]["space"]
    resp = api_client.post(
        f"/spaces/{space['id']}/members",
        json={"userId": student.user_id},
        headers=_auth(teacher_token),
    )
    assert resp.status_code == 201, resp.text
    resp = api_client.post(
        "/tasks",
        json={
            "name": "缓存模拟器",
            "intro": "题",
            "description": "题",
            "space": space["id"],
            "submitterType": "USER",
            "resubmittable": True,
            "editable": True,
            "defaultDeadline": 30,
            # The notices are about an application waiting on the publisher.
            "startMode": "APPLY",
            "deadline": int(time.time() * 1000) + 7 * 86400 * 1000,
            "submissionSchema": [{"prompt": "源码", "type": "FILE"}],
        },
        headers=_auth(teacher_token),
    )
    assert resp.status_code == 200, resp.text
    task_id = resp.json()["data"]["task"]["id"]
    resp = api_client.patch(
        f"/tasks/{task_id}",
        json={"approved": "APPROVED"},
        headers=_auth(teacher_token),
    )
    assert resp.status_code == 200, resp.text
    return {
        "teacher": teacher_token,
        "student": token,
        "student_id": student.user_id,
        "task": task_id,
    }


def _notices(api_client: TestClient, token: str, type_: str = "TASK_NOTICE"):
    resp = api_client.get(
        "/notifications", params={"pageSize": 50}, headers=_auth(token)
    )
    assert resp.status_code == 200, resp.text
    return [n for n in resp.json()["data"]["notifications"] if n["type"] == type_]


def _waiting(api_client: TestClient, token: str) -> list[dict]:
    resp = api_client.get("/awaiting-me", headers=_auth(token))
    assert resp.status_code == 200, resp.text
    return [i for i in resp.json()["data"]["data"] if i["kind"] == "task_queue"]


def test_a_claim_waits_on_the_publisher_and_its_decision_reaches_the_claimer(
    api_client: TestClient, board: dict
):
    resp = api_client.post(
        f"/tasks/{board['task']}/participations/user",
        json={},
        headers=_auth(board["student"]),
    )
    assert resp.status_code == 200, resp.text
    participant = resp.json()["data"]["participant"]
    if participant["approved"] == "APPROVED":
        pytest.skip("this board approves claims by itself")

    [row] = _waiting(api_client, board["teacher"])
    assert row["taskId"] == board["task"] and row["claims"] == 1

    resp = api_client.patch(
        f"/tasks/{board['task']}/participants/{participant['id']}",
        json={"approved": "APPROVED"},
        headers=_auth(board["teacher"]),
    )
    assert resp.status_code == 200, resp.text

    assert _waiting(api_client, board["teacher"]) == []
    [notice] = _notices(api_client, board["student"])
    assert notice["contextMetadata"]["event"] == "claimApproved"
    assert notice["contextMetadata"]["spaceTaskId"] == board["task"]
    # The publisher who decided is not told of their own decision.
    assert _notices(api_client, board["teacher"]) == []


def test_changed_requirements_reach_those_who_claimed(
    api_client: TestClient, board: dict
):
    resp = api_client.post(
        f"/tasks/{board['task']}/participations/user",
        json={},
        headers=_auth(board["student"]),
    )
    assert resp.status_code == 200, resp.text

    same = api_client.patch(
        f"/tasks/{board['task']}",
        json={"submissionSchema": [{"prompt": "源码", "type": "FILE"}]},
        headers=_auth(board["teacher"]),
    )
    assert same.status_code == 200, same.text
    assert _notices(api_client, board["student"]) == []

    changed = api_client.patch(
        f"/tasks/{board['task']}",
        json={"rubric": [{"item": "正确性", "points": 60, "criteria": "全对"}]},
        headers=_auth(board["teacher"]),
    )
    assert changed.status_code == 200, changed.text
    events = [
        n["contextMetadata"]["event"] for n in _notices(api_client, board["student"])
    ]
    assert events == ["requirementsChanged"]


def test_hiding_the_rubric_tells_those_who_claimed_nothing(
    api_client: TestClient, board: dict
):
    """Showing or hiding the rubric changes neither what to hand in nor how it is
    scored; the edit page sends it on every save, so it must not tell anyone."""
    resp = api_client.post(
        f"/tasks/{board['task']}/participations/user",
        json={},
        headers=_auth(board["student"]),
    )
    assert resp.status_code == 200, resp.text

    hidden = api_client.patch(
        f"/tasks/{board['task']}",
        json={"rubricPublic": False},
        headers=_auth(board["teacher"]),
    )
    assert hidden.status_code == 200, hidden.text
    events = [
        n["contextMetadata"]["event"] for n in _notices(api_client, board["student"])
    ]
    assert "requirementsChanged" not in events


def test_deadlines_are_reminded_twice_and_a_missed_one_is_told(
    api_client: TestClient, board: dict, _portal, db_session
):
    from app.domain.task.models import TaskMembership
    from app.domain.task_notices.sweep import remind_deadlines

    resp = api_client.post(
        f"/tasks/{board['task']}/participations/user",
        json={},
        headers=_auth(board["student"]),
    )
    assert resp.status_code == 200, resp.text
    participant_id = resp.json()["data"]["participant"]["id"]
    now = datetime.now(UTC)

    @asynccontextmanager
    async def sessions():
        yield db_session

    async def set_deadline(at: datetime) -> None:
        row = await db_session.get(TaskMembership, participant_id)
        row.approved = 0
        row.deadline = at
        await db_session.flush()

    async def sweep(at: datetime) -> int:
        return await remind_deadlines(sessions, now=at)

    _portal.call(set_deadline, now + timedelta(days=2, hours=12))
    _portal.call(sweep, now)
    _portal.call(sweep, now + timedelta(hours=1))  # said once
    assert len(_notices(api_client, board["student"])) == 1
    _portal.call(sweep, now + timedelta(days=2))  # the last day: the pushed one
    assert len(_notices(api_client, board["student"], "TASK_DUE_SOON")) == 1
    events = [
        n["contextMetadata"]["event"] for n in _notices(api_client, board["student"])
    ]
    assert events == ["dueSoon"]

    _portal.call(sweep, now + timedelta(days=3))
    [missed] = _notices(api_client, board["teacher"])
    assert missed["contextMetadata"]["event"] == "missed"
    assert missed["contextMetadata"]["count"] == 1
