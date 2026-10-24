"""A task's 阶段: set by its publisher, handed in and graded stage by stage.

A stage is done once one of its submissions is accepted, and a claim is done
when every stage is. Each stage closes at its own due date. A stage keeps its
id however the list is reordered, and what was handed in follows it; a stage
that holds submissions cannot be dropped. A task without stages is one stage.
"""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update

from app.domain.task.deadline_scheduler import check_and_fail_expired_deadlines
from app.domain.task.models import TaskMembership
from tests.integration.conftest import UserCreator, create_approved_space, unique_int
from tests.integration.test_task_drafts import _as_cheese

DAY_MS = 24 * 3600 * 1000


def _ms(days: float) -> int:
    return int((datetime.now(UTC).timestamp()) * 1000 + days * DAY_MS)


def _login(user_client: UserCreator, api_client: TestClient):
    user = user_client.create_user()
    user.token = user_client.login(api_client, user.username, user.password)
    return user


def _headers(user) -> dict:
    return {"Authorization": f"Bearer {user.token}"}


# The ids the publisher's form gives the two stages; items and lines name
# their stage by them.
NEED = "need"
IMPL = "impl"
STAGES = [
    {"id": NEED, "name": "需求分析", "description": "交一份需求文档", "dueAt": None},
    {"id": IMPL, "name": "实现", "description": "交代码", "dueAt": None},
]
SCHEMA = [
    {"prompt": "需求文档", "type": "TEXT", "stage": NEED},
    {"prompt": "代码", "type": "TEXT", "stage": IMPL},
    {"prompt": "说明", "type": "TEXT", "stage": IMPL},
]
RUBRIC = [
    {"item": "完整", "points": 40, "criteria": "", "stage": NEED},
    {"item": "正确", "points": 60, "criteria": "", "stage": IMPL},
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
        "rank": 2,
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
        params={"stage": stage} if stage is not None else {},
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
    assert [s["id"] for s in data["stages"]] == [NEED, IMPL]
    assert [e["stage"] for e in data["submissionSchema"]] == [NEED, IMPL, IMPL]
    assert [r["stage"] for r in data["rubric"]] == [NEED, IMPL]


def test_each_stage_is_graded_on_its_own_and_the_claim_is_done_when_all_are(
    api_client, board
):
    task_id = _publish(api_client, board)
    membership_id = _claim(api_client, board, task_id)

    first = _submit(api_client, board, task_id, membership_id, NEED, ["需求"])
    assert first.status_code == 200, first.text
    submission = first.json()["data"]["submission"]
    assert submission["stage"] == NEED
    assert submission["content"][0]["title"] == "需求文档"
    _review(api_client, board, task_id, membership_id, submission["id"], True)

    mine = _progress(api_client, board["student"], task_id)["mine"]
    assert mine["stages"] == ["PASSED", "NOT_SUBMITTED"]
    # One stage passed is not the task passed.
    assert _completion(api_client, board, task_id, membership_id) == "NOT_SUBMITTED"

    second = _submit(api_client, board, task_id, membership_id, IMPL, ["代码", "说明"])
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

    again = _submit(api_client, board, task_id, membership_id, IMPL, ["代码 2", "说明"])
    _review(
        api_client,
        board,
        task_id,
        membership_id,
        again.json()["data"]["submission"]["id"],
        True,
    )
    assert _completion(api_client, board, task_id, membership_id) == "SUCCESS"


def test_a_passed_task_stays_passed_through_a_later_version(
    api_client, board, db_session, _portal
):
    """Unstaged, as before stages existed: v1 accepted, v2 handed in and sent
    back, the deadline passes — what was accepted stays accepted, and the
    sweep does not fail the claim."""
    task_id = _publish(
        api_client,
        board,
        stages=[],
        submissionSchema=[{"prompt": "作业", "type": "TEXT"}],
        rubric=[],
    )
    membership_id = _claim(api_client, board, task_id)
    first = _submit(api_client, board, task_id, membership_id, None, ["v1"])
    _review(api_client, board, task_id, membership_id, _id(first), True)
    second = _submit(api_client, board, task_id, membership_id, None, ["v2"])
    assert _completion(api_client, board, task_id, membership_id) == "SUCCESS"
    _review(api_client, board, task_id, membership_id, _id(second), False)
    assert _completion(api_client, board, task_id, membership_id) == "SUCCESS"

    async def past_and_sweep():
        await db_session.execute(
            update(TaskMembership)
            .where(TaskMembership.id == membership_id)
            .values(deadline=datetime.now(UTC) - timedelta(days=1))
        )
        await db_session.flush()
        await check_and_fail_expired_deadlines(db_session)

    _portal.call(past_and_sweep)
    db_session.expire_all()
    assert _completion(api_client, board, task_id, membership_id) == "SUCCESS"
    assert _progress(api_client, board["student"], task_id)["mine"]["stages"] == [
        "PASSED"
    ]


def _id(response) -> int:
    assert response.status_code == 200, response.text
    return int(response.json()["data"]["submission"]["id"])


def _read(api_client, board, task_id) -> dict:
    resp = api_client.get(f"/tasks/{task_id}", headers=_headers(board["teacher"]))
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["task"]


def test_the_publisher_sees_how_the_class_stands_on_each_stage(api_client, board):
    task_id = _publish(api_client, board)
    membership_id = _claim(api_client, board, task_id)
    _submit(api_client, board, task_id, membership_id, NEED, ["需求"])

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

    late = _submit(api_client, board, task_id, membership_id, NEED, ["需求"])
    assert late.status_code == 400, late.text
    on_time = _submit(api_client, board, task_id, membership_id, IMPL, ["代码", "说明"])
    assert on_time.status_code == 200, on_time.text
    unknown = _submit(api_client, board, task_id, membership_id, "nope", ["?"])
    assert unknown.status_code == 400, unknown.text


def test_reordering_stages_carries_what_was_handed_in(api_client, board):
    task_id = _publish(api_client, board)
    membership_id = _claim(api_client, board, task_id)
    _submit(api_client, board, task_id, membership_id, NEED, ["需求"])

    first, second = _read(api_client, board, task_id)["stages"]
    swapped = api_client.patch(
        f"/tasks/{task_id}",
        json={"stages": [second, first]},
        headers=_headers(board["teacher"]),
    )
    assert swapped.status_code == 200, swapped.text
    task = swapped.json()["data"]["task"]
    assert [s["name"] for s in task["stages"]] == ["实现", "需求分析"]
    # The form and rubric moved with their stages.
    by_stage = {e["prompt"]: e["stage"] for e in task["submissionSchema"]}
    assert by_stage == {"需求文档": NEED, "代码": IMPL, "说明": IMPL}
    # So did the submission: the requirements stage is now second.
    assert _progress(api_client, board["student"], task_id)["mine"]["stages"] == [
        "NOT_SUBMITTED",
        "PENDING_REVIEW",
    ]


def test_a_stage_holding_submissions_cannot_be_dropped(api_client, board):
    task_id = _publish(api_client, board)
    membership_id = _claim(api_client, board, task_id)
    _submit(api_client, board, task_id, membership_id, NEED, ["需求"])

    _, second = _read(api_client, board, task_id)["stages"]
    dropped = api_client.patch(
        f"/tasks/{task_id}",
        json={"stages": [second]},
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
            "submissionSchema": [{"prompt": "a", "type": "TEXT", "stage": "nope"}],
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
    resp = _submit(api_client, board, task_id, membership_id, None, ["作业"])
    assert resp.status_code == 200, resp.text
    progress = _progress(api_client, board["student"], task_id)
    assert progress["stages"] == []
    assert progress["mine"]["stages"] == ["PENDING_REVIEW"]
    assert _completion(api_client, board, task_id, membership_id) == "PENDING_REVIEW"


def test_the_first_stage_added_to_a_task_with_hand_ins_takes_them(api_client, board):
    """Before it had stages the task was one stage; the first stage it gets is
    that stage, whatever the writer says about where it came from."""
    task_id = _publish(
        api_client,
        board,
        stages=[],
        submissionSchema=[{"prompt": "代码", "type": "TEXT"}],
        rubric=[],
    )
    membership_id = _claim(api_client, board, task_id)
    _id(_submit(api_client, board, task_id, membership_id, None, ["hello"]))

    resp = api_client.patch(
        f"/tasks/{task_id}",
        json={
            "stages": [
                {"id": "one", "name": "第一阶段", "description": "", "dueAt": None},
                {"id": "two", "name": "第二阶段", "description": "", "dueAt": None},
            ],
            "submissionSchema": [
                {"prompt": "代码", "type": "TEXT", "stage": "one"},
                {"prompt": "报告", "type": "TEXT", "stage": "two"},
            ],
        },
        headers=_headers(board["teacher"]),
    )
    assert resp.status_code == 200, resp.text
    assert _progress(api_client, board["student"], task_id)["mine"]["stages"] == [
        "PENDING_REVIEW",
        "NOT_SUBMITTED",
    ]


def _cheese_edits(api_client, board, task_id, stages) -> int:
    """芝士 writes the stages into the task's changes draft, then saves them."""
    teacher = board["teacher"]
    written = api_client.post(
        "/task-drafts/agent/write",
        json={"task": task_id, "stages": stages},
        headers=_as_cheese(teacher),
    )
    assert written.status_code == 200, written.text
    return api_client.post(
        "/task-drafts/agent/publish",
        json={"draft": written.json()["data"]["draft"]},
        headers=_as_cheese(teacher),
    )


def _as_written(stage: dict) -> dict:
    """A stage as the draft view shows it to 芝士, in its words."""
    return {
        "id": stage["id"],
        "name": stage["name"],
        "description": stage["description"],
    }


def test_a_stage_reordered_through_a_draft_keeps_its_hand_ins(api_client, board):
    task_id = _publish(api_client, board)
    membership_id = _claim(api_client, board, task_id)
    _id(_submit(api_client, board, task_id, membership_id, NEED, ["需求"]))
    first, second = _read(api_client, board, task_id)["stages"]

    saved = _cheese_edits(
        api_client, board, task_id, [_as_written(second), _as_written(first)]
    )
    assert saved.status_code == 200, saved.text
    task = _read(api_client, board, task_id)
    assert [s["name"] for s in task["stages"]] == ["实现", "需求分析"]
    # What is scored and what is handed in stay with their stage.
    assert {r["item"]: r["stage"] for r in task["rubric"]} == {
        "完整": NEED,
        "正确": IMPL,
    }
    assert {e["prompt"]: e["stage"] for e in task["submissionSchema"]} == {
        "需求文档": NEED,
        "代码": IMPL,
        "说明": IMPL,
    }
    assert _progress(api_client, board["student"], task_id)["mine"]["stages"] == [
        "NOT_SUBMITTED",
        "PENDING_REVIEW",
    ]


def test_a_stage_dropped_through_a_draft_while_it_holds_hand_ins_is_refused(
    api_client, board
):
    task_id = _publish(api_client, board)
    membership_id = _claim(api_client, board, task_id)
    _id(_submit(api_client, board, task_id, membership_id, NEED, ["需求"]))
    _, second = _read(api_client, board, task_id)["stages"]

    saved = _cheese_edits(api_client, board, task_id, [_as_written(second)])
    assert saved.status_code == 400, saved.text
    assert saved.json()["error"]["i18n"]["key"] == "stageHasSubmissions"
    assert len(_read(api_client, board, task_id)["stages"]) == 2
    assert _progress(api_client, board["student"], task_id)["mine"]["stages"] == [
        "PENDING_REVIEW",
        "NOT_SUBMITTED",
    ]


def test_an_earlier_stage_still_waits_in_the_queue_after_a_later_one_is_reviewed(
    api_client, board
):
    task_id = _publish(api_client, board)
    membership_id = _claim(api_client, board, task_id)
    _id(_submit(api_client, board, task_id, membership_id, NEED, ["需求"]))
    later = _submit(api_client, board, task_id, membership_id, IMPL, ["代码", "说明"])
    _review(api_client, board, task_id, membership_id, _id(later), True)

    waiting = api_client.get("/awaiting-me", headers=_headers(board["teacher"]))
    assert waiting.status_code == 200, waiting.text
    queued = [
        item
        for item in waiting.json()["data"]["data"]
        if item["kind"] == "task_queue" and item["taskId"] == task_id
    ]
    assert [item["reviews"] for item in queued] == [1]


def test_a_stage_added_past_its_due_date_unpasses_a_finished_claim(api_client, board):
    """Both stages passed; the publisher adds a third that is already due. The
    claim can no longer finish, and what it reads says so at once."""
    task_id = _publish(api_client, board)
    membership_id = _claim(api_client, board, task_id)
    first = _submit(api_client, board, task_id, membership_id, NEED, ["需求"])
    _review(api_client, board, task_id, membership_id, _id(first), True)
    second = _submit(api_client, board, task_id, membership_id, IMPL, ["代码", "说明"])
    _review(api_client, board, task_id, membership_id, _id(second), True)
    assert _completion(api_client, board, task_id, membership_id) == "SUCCESS"

    stages = _read(api_client, board, task_id)["stages"]
    late = {"id": None, "name": "答辩", "description": "", "dueAt": _ms(-1)}
    resp = api_client.patch(
        f"/tasks/{task_id}",
        json={"stages": [*stages, late]},
        headers=_headers(board["teacher"]),
    )
    assert resp.status_code == 200, resp.text
    assert _completion(api_client, board, task_id, membership_id) == "FAILED"


def test_dropping_the_stage_a_claim_had_left_finishes_it(api_client, board):
    """The first stage passed and the second is empty; the publisher drops the
    second. Every stage the task has is passed, and the claim reads so."""
    task_id = _publish(api_client, board)
    membership_id = _claim(api_client, board, task_id)
    first = _submit(api_client, board, task_id, membership_id, NEED, ["需求"])
    _review(api_client, board, task_id, membership_id, _id(first), True)
    assert _completion(api_client, board, task_id, membership_id) == "NOT_SUBMITTED"

    kept, _ = _read(api_client, board, task_id)["stages"]
    resp = api_client.patch(
        f"/tasks/{task_id}",
        json={"stages": [kept]},
        headers=_headers(board["teacher"]),
    )
    assert resp.status_code == 200, resp.text
    assert _completion(api_client, board, task_id, membership_id) == "SUCCESS"


def test_cheese_renaming_a_published_task_leaves_its_form_as_it_is(api_client, board):
    """A change draft starts as the task is: saving one changed field leaves
    the submission items, rubric and stages as they were."""
    task_id = _publish(api_client, board)
    before = _read(api_client, board, task_id)
    written = api_client.post(
        "/task-drafts/agent/write",
        json={"task": task_id, "name": "改过名字的实验"},
        headers=_as_cheese(board["teacher"]),
    )
    assert written.status_code == 200, written.text
    saved = api_client.post(
        "/task-drafts/agent/publish",
        json={"draft": written.json()["data"]["draft"]},
        headers=_as_cheese(board["teacher"]),
    )
    assert saved.status_code == 200, saved.text

    after = _read(api_client, board, task_id)
    assert after["name"] == "改过名字的实验"
    assert after["submissionSchema"] == before["submissionSchema"]
    assert after["rubric"] == before["rubric"]
    assert after["stages"] == before["stages"]
