"""A task's 阶段: set by its publisher, handed in and graded stage by stage.

A stage is done when its latest submission is accepted, and a claim is done
when every stage is. Each stage closes at its own due date. Reordering the
stages carries what was already handed in with them; a stage that holds
submissions cannot be dropped. A task without stages is one stage.
"""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from tests.integration.conftest import UserCreator, create_approved_space, unique_int

DAY_MS = 24 * 3600 * 1000


def _ms(days: float) -> int:
    return int((datetime.now(UTC).timestamp()) * 1000 + days * DAY_MS)


def _login(user_client: UserCreator, api_client: TestClient):
    user = user_client.create_user()
    user.token = user_client.login(api_client, user.username, user.password)
    return user


def _headers(user) -> dict:
    return {"Authorization": f"Bearer {user.token}"}


STAGES = [
    {"name": "需求分析", "description": "交一份需求文档", "dueAt": None},
    {"name": "实现", "description": "交代码", "dueAt": None},
]
SCHEMA = [
    {"prompt": "需求文档", "type": "TEXT", "stage": 0},
    {"prompt": "代码", "type": "TEXT", "stage": 1},
    {"prompt": "说明", "type": "TEXT", "stage": 1},
]
RUBRIC = [
    {"item": "完整", "points": 40, "criteria": "", "stage": 0},
    {"item": "正确", "points": 60, "criteria": "", "stage": 1},
]


@pytest.fixture
def board(user_client: UserCreator, api_client: TestClient) -> dict:
    teacher = _login(user_client, api_client)
    student = _login(user_client, api_client)
    suffix = unique_int(10000000, 99999999)
    space = create_approved_space(
        api_client,
        json={
            "name": f"Stages Space ({suffix})",
            "intro": "stages",
            "description": "A lengthy description. " * 20,
            "avatarId": 1,
            "enableRank": False,
            "taskTemplates": [],
        },
        headers=_headers(teacher),
    ).json()["data"]["space"]
    return {"teacher": teacher, "student": student, "space": space}


def _publish(api_client: TestClient, board: dict, **fields) -> int:
    body = {
        "name": f"Lab ({unique_int(1000, 9999)})",
        "submitterType": "USER",
        "deadline": _ms(30),
        "resubmittable": True,
        "editable": True,
        "intro": "intro " * 5,
        "description": "description " * 10,
        "space": board["space"]["id"],
        "categoryId": board["space"]["defaultCategoryId"],
        "submissionSchema": SCHEMA,
        "rubric": RUBRIC,
        "stages": STAGES,
    }
    body.update(fields)
    resp = api_client.post("/tasks", json=body, headers=_headers(board["teacher"]))
    assert resp.status_code == 200, resp.text
    task_id = int(resp.json()["data"]["task"]["id"])
    resp = api_client.patch(
        f"/tasks/{task_id}",
        json={"approved": "APPROVED"},
        headers=_headers(board["teacher"]),
    )
    assert resp.status_code == 200, resp.text
    return task_id


def _claim(api_client: TestClient, board: dict, task_id: int) -> int:
    student = board["student"]
    resp = api_client.post(
        f"/tasks/{task_id}/participants",
        params={"member": student.user_id},
        json={},
        headers=_headers(student),
    )
    assert resp.status_code == 200, resp.text
    membership_id = int(resp.json()["data"]["participant"]["id"])
    resp = api_client.patch(
        f"/tasks/{task_id}/participants/{membership_id}",
        json={"approved": "APPROVED"},
        headers=_headers(board["teacher"]),
    )
    assert resp.status_code == 200, resp.text
    return membership_id


def _submit(api_client, board, task_id, membership_id, stage, texts):
    return api_client.post(
        f"/tasks/{task_id}/participants/{membership_id}/submissions",
        params={"stage": stage},
        json=[{"text": t} for t in texts],
        headers=_headers(board["student"]),
    )


def _review(api_client, board, task_id, membership_id, submission_id, accepted):
    resp = api_client.post(
        f"/tasks/{task_id}/participants/{membership_id}/submissions/{submission_id}/review",
        json={"accepted": accepted, "score": 1, "comment": ""},
        headers=_headers(board["teacher"]),
    )
    assert resp.status_code == 200, resp.text


def _progress(api_client, user, task_id) -> dict:
    resp = api_client.get(f"/tasks/{task_id}/progress", headers=_headers(user))
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def _completion(api_client, board, task_id, membership_id) -> str:
    resp = api_client.get(
        f"/tasks/{task_id}/participants/{membership_id}",
        headers=_headers(board["teacher"]),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["participant"]["completionStatus"]


def test_a_published_task_reads_back_its_stages_and_what_belongs_to_each(
    api_client, board
):
    task_id = _publish(
        api_client, board, stages=[{**STAGES[0], "dueAt": _ms(3)}, STAGES[1]]
    )
    task = api_client.get(f"/tasks/{task_id}", headers=_headers(board["student"]))
    assert task.status_code == 200, task.text
    data = task.json()["data"]["task"]
    assert [s["name"] for s in data["stages"]] == ["需求分析", "实现"]
    assert data["stages"][0]["dueAt"] is not None
    assert data["stages"][1]["dueAt"] is None
    assert [e["stage"] for e in data["submissionSchema"]] == [0, 1, 1]
    assert [r["stage"] for r in data["rubric"]] == [0, 1]


def test_each_stage_is_graded_on_its_own_and_the_claim_is_done_when_all_are(
    api_client, board
):
    task_id = _publish(api_client, board)
    membership_id = _claim(api_client, board, task_id)

    first = _submit(api_client, board, task_id, membership_id, 0, ["需求"])
    assert first.status_code == 200, first.text
    submission = first.json()["data"]["submission"]
    assert submission["stage"] == 0
    assert submission["content"][0]["title"] == "需求文档"
    _review(api_client, board, task_id, membership_id, submission["id"], True)

    mine = _progress(api_client, board["student"], task_id)["mine"]
    assert mine["stages"] == ["PASSED", "NOT_SUBMITTED"]
    # One stage passed is not the task passed.
    assert _completion(api_client, board, task_id, membership_id) == "NOT_SUBMITTED"

    second = _submit(api_client, board, task_id, membership_id, 1, ["代码", "说明"])
    assert second.status_code == 200, second.text
    # The second stage's items name its own form items, not the first stage's.
    assert [c["title"] for c in second.json()["data"]["submission"]["content"]] == [
        "代码",
        "说明",
    ]
    assert _completion(api_client, board, task_id, membership_id) == "PENDING_REVIEW"
    _review(
        api_client,
        board,
        task_id,
        membership_id,
        second.json()["data"]["submission"]["id"],
        False,
    )
    assert _progress(api_client, board["student"], task_id)["mine"]["stages"] == [
        "PASSED",
        "REJECTED",
    ]

    again = _submit(api_client, board, task_id, membership_id, 1, ["代码 2", "说明"])
    _review(
        api_client,
        board,
        task_id,
        membership_id,
        again.json()["data"]["submission"]["id"],
        True,
    )
    assert _completion(api_client, board, task_id, membership_id) == "SUCCESS"


def test_a_stage_is_judged_by_its_latest_submission(api_client, board):
    task_id = _publish(api_client, board)
    membership_id = _claim(api_client, board, task_id)
    first = _submit(api_client, board, task_id, membership_id, 0, ["v1"])
    _review(
        api_client,
        board,
        task_id,
        membership_id,
        first.json()["data"]["submission"]["id"],
        True,
    )
    _submit(api_client, board, task_id, membership_id, 0, ["v2"])
    assert _progress(api_client, board["student"], task_id)["mine"]["stages"][0] == (
        "PENDING_REVIEW"
    )


def test_the_publisher_sees_how_the_class_stands_on_each_stage(api_client, board):
    task_id = _publish(api_client, board)
    membership_id = _claim(api_client, board, task_id)
    _submit(api_client, board, task_id, membership_id, 0, ["需求"])

    teacher_view = _progress(api_client, board["teacher"], task_id)
    assert teacher_view["distribution"][0]["PENDING_REVIEW"] == 1
    assert teacher_view["distribution"][1]["NOT_SUBMITTED"] == 1
    # A student does not see the class.
    assert _progress(api_client, board["student"], task_id)["distribution"] is None


def test_a_stage_past_its_due_date_takes_no_more_submissions(api_client, board):
    task_id = _publish(
        api_client, board, stages=[{**STAGES[0], "dueAt": _ms(-1)}, STAGES[1]]
    )
    membership_id = _claim(api_client, board, task_id)

    late = _submit(api_client, board, task_id, membership_id, 0, ["需求"])
    assert late.status_code == 400, late.text
    on_time = _submit(api_client, board, task_id, membership_id, 1, ["代码", "说明"])
    assert on_time.status_code == 200, on_time.text
    unknown = _submit(api_client, board, task_id, membership_id, 5, ["?"])
    assert unknown.status_code == 400, unknown.text


def test_reordering_stages_carries_what_was_handed_in(api_client, board):
    task_id = _publish(api_client, board)
    membership_id = _claim(api_client, board, task_id)
    _submit(api_client, board, task_id, membership_id, 0, ["需求"])

    swapped = api_client.patch(
        f"/tasks/{task_id}",
        json={
            "stages": [
                {**STAGES[1], "previousIndex": 1},
                {**STAGES[0], "previousIndex": 0},
            ]
        },
        headers=_headers(board["teacher"]),
    )
    assert swapped.status_code == 200, swapped.text
    task = swapped.json()["data"]["task"]
    assert [s["name"] for s in task["stages"]] == ["实现", "需求分析"]
    # The form and rubric moved with their stages.
    by_stage = {e["prompt"]: e["stage"] for e in task["submissionSchema"]}
    assert by_stage == {"需求文档": 1, "代码": 0, "说明": 0}
    # So did the submission: the requirements stage is now second.
    assert _progress(api_client, board["student"], task_id)["mine"]["stages"] == [
        "NOT_SUBMITTED",
        "PENDING_REVIEW",
    ]


def test_a_stage_holding_submissions_cannot_be_dropped(api_client, board):
    task_id = _publish(api_client, board)
    membership_id = _claim(api_client, board, task_id)
    _submit(api_client, board, task_id, membership_id, 0, ["需求"])

    dropped = api_client.patch(
        f"/tasks/{task_id}",
        json={"stages": [{**STAGES[1], "previousIndex": 1}]},
        headers=_headers(board["teacher"]),
    )
    assert dropped.status_code == 400, dropped.text
    task = api_client.get(f"/tasks/{task_id}", headers=_headers(board["teacher"]))
    assert len(task.json()["data"]["task"]["stages"]) == 2


def test_an_item_naming_a_stage_the_task_does_not_have_is_refused(api_client, board):
    resp = api_client.post(
        "/tasks",
        json={
            "name": "Lab",
            "submitterType": "USER",
            "resubmittable": True,
            "editable": True,
            "intro": "intro",
            "description": "description",
            "space": board["space"]["id"],
            "categoryId": board["space"]["defaultCategoryId"],
            "submissionSchema": [{"prompt": "a", "type": "TEXT", "stage": 1}],
        },
        headers=_headers(board["teacher"]),
    )
    assert resp.status_code == 400, resp.text


def test_a_task_without_stages_is_one_stage(api_client, board):
    task_id = _publish(
        api_client,
        board,
        stages=[],
        submissionSchema=[{"prompt": "作业", "type": "TEXT"}],
        rubric=[],
    )
    membership_id = _claim(api_client, board, task_id)
    resp = _submit(api_client, board, task_id, membership_id, 0, ["作业"])
    assert resp.status_code == 200, resp.text
    progress = _progress(api_client, board["student"], task_id)
    assert progress["stages"] == []
    assert progress["mine"]["stages"] == ["PENDING_REVIEW"]
    assert _completion(api_client, board, task_id, membership_id) == "PENDING_REVIEW"
