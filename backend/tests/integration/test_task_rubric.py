"""A task's 评分要点: written with the task, replaced on edit, always read by the
graders and read by the students only when the task makes it public."""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from tests.integration.conftest import UserCreator, create_approved_space, unique_int

RUBRIC = [
    {"item": "正确性", "points": 60, "criteria": "8 组访存记录全对", "stage": None},
    {"item": "实现", "points": 20, "criteria": "LRU 替换正确", "stage": None},
    {"item": "报告", "points": 20, "criteria": "", "stage": None},
]


def _login(user_client: UserCreator, api_client: TestClient):
    user = user_client.create_user()
    user.token = user_client.login(api_client, user.username, user.password)
    return user


def _headers(user) -> dict:
    return {"Authorization": f"Bearer {user.token}"}


@pytest.fixture
def board(user_client: UserCreator, api_client: TestClient) -> dict:
    creator = _login(user_client, api_client)
    student = _login(user_client, api_client)
    suffix = unique_int(10000000, 99999999)
    space = create_approved_space(
        api_client,
        json={
            "name": f"Rubric Space ({suffix})",
            "intro": "rubric",
            "description": "A lengthy description. " * 20,
            "avatarId": 1,
            "enableRank": False,
            "taskTemplates": [],
        },
        headers=_headers(creator),
    ).json()["data"]["space"]
    joined = api_client.post(
        f"/spaces/{space['id']}/members",
        json={"userId": student.user_id},
        headers=_headers(creator),
    )
    assert joined.status_code == 201, joined.text
    return {"creator": creator, "student": student, "space": space}


def _create(api_client: TestClient, board: dict, **extra) -> dict:
    resp = api_client.post(
        "/tasks",
        json={
            "name": f"Lab 3 ({unique_int(1000, 9999)})",
            "submitterType": "USER",
            "deadline": int((datetime.now(UTC).timestamp() + 7 * 24 * 3600) * 1000),
            "resubmittable": True,
            "editable": True,
            "intro": "Cache simulator " * 5,
            "description": "Write csim " * 10,
            "submissionSchema": [{"prompt": "csim.c", "type": "FILE"}],
            "space": board["space"]["id"],
            "categoryId": board["space"]["defaultCategoryId"],
            **extra,
        },
        headers=_headers(board["creator"]),
    )
    return resp


def _approve(api_client: TestClient, board: dict, task_id: int) -> None:
    resp = api_client.patch(
        f"/tasks/{task_id}",
        json={"approved": "APPROVED"},
        headers=_headers(board["creator"]),
    )
    assert resp.status_code == 200, resp.text


def _read(api_client: TestClient, user, task_id: int) -> dict:
    resp = api_client.get(f"/tasks/{task_id}", headers=_headers(user))
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["task"]


def test_a_public_rubric_is_read_by_everyone(api_client: TestClient, board: dict):
    resp = _create(api_client, board, rubric=RUBRIC)
    assert resp.status_code == 200, resp.text
    task_id = resp.json()["data"]["task"]["id"]
    _approve(api_client, board, task_id)

    task = _read(api_client, board["student"], task_id)
    assert task["rubricPublic"] is True
    assert task["rubric"] == RUBRIC


def test_a_private_rubric_is_read_only_by_the_graders(
    api_client: TestClient, board: dict
):
    task_id = _create(api_client, board, rubric=RUBRIC, rubricPublic=False).json()[
        "data"
    ]["task"]["id"]
    _approve(api_client, board, task_id)

    assert _read(api_client, board["creator"], task_id)["rubric"] == RUBRIC
    seen = _read(api_client, board["student"], task_id)
    assert seen["rubricPublic"] is False
    assert seen["rubric"] == []


def test_editing_replaces_the_rubric_and_its_visibility(
    api_client: TestClient, board: dict
):
    task_id = _create(api_client, board, rubric=RUBRIC, rubricPublic=False).json()[
        "data"
    ]["task"]["id"]
    _approve(api_client, board, task_id)

    resp = api_client.patch(
        f"/tasks/{task_id}",
        json={
            "rubric": [{"item": "正确性", "points": 100, "criteria": "全对"}],
            "rubricPublic": True,
        },
        headers=_headers(board["creator"]),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["task"]["rubric"] == [
        {"item": "正确性", "points": 100, "criteria": "全对", "stage": None}
    ]
    assert _read(api_client, board["student"], task_id)["rubric"] == [
        {"item": "正确性", "points": 100, "criteria": "全对", "stage": None}
    ]

    # An edit that leaves the rubric out leaves it as it is.
    api_client.patch(
        f"/tasks/{task_id}",
        json={"name": "Lab 3 renamed"},
        headers=_headers(board["creator"]),
    )
    assert len(_read(api_client, board["creator"], task_id)["rubric"]) == 1


def test_empty_rows_are_dropped_and_bad_rows_refused(
    api_client: TestClient, board: dict
):
    resp = _create(
        api_client,
        board,
        rubric=[{"item": "", "points": "", "criteria": ""}, RUBRIC[0]],
    )
    assert resp.status_code == 200, resp.text
    task_id = resp.json()["data"]["task"]["id"]
    assert _read(api_client, board["creator"], task_id)["rubric"] == [RUBRIC[0]]

    for bad in (
        {"item": "", "points": 10, "criteria": "no name"},
        {"item": "超出", "points": 5000, "criteria": ""},
        {"item": "不是数", "points": "many", "criteria": ""},
    ):
        assert _create(api_client, board, rubric=[bad]).status_code == 400
