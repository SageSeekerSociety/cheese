"""A task's 阶段: set by its publisher, handed in and graded stage by stage.

A stage is done once one of its submissions is accepted, and a claim is done
when every stage is. Each stage closes at its own due date. A stage keeps its
id however the list is reordered, and what was handed in follows it; a stage
that holds submissions cannot be dropped. A task without stages is one stage.
"""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, update

from app.domain.space.models import SpaceAdminRelation
from app.domain.task import package
from app.domain.task.deadline_scheduler import check_and_fail_expired_deadlines
from app.domain.task.models import Task, TaskMembership
from tests.integration.conftest import (
    UserCreator,
    create_approved_space,
    unique_int,
)
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
    joined = api_client.post(
        f"/spaces/{space['id']}/members",
        json={"userId": student.user_id},
        headers=_headers(teacher),
    )
    assert joined.status_code == 201, joined.text
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
            "stages": STAGES,
            "submissionSchema": [{"prompt": "a", "type": "TEXT", "stage": "nope"}],
        },
        headers=_headers(board["teacher"]),
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["error"]["i18n"]["key"] == "stageReferenceUnknown"


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


def _cheese_writes(api_client, board, **args) -> dict:
    written = api_client.post(
        "/task-drafts/agent/write", json=args, headers=_as_cheese(board["teacher"])
    )
    assert written.status_code == 200, written.text
    return written.json()["data"]


def _cheese_publishes(api_client, board, draft_id: int):
    return api_client.post(
        "/task-drafts/agent/publish",
        json={"draft": draft_id},
        headers=_as_cheese(board["teacher"]),
    )


TWO_STAGES = [
    {"name": "需求分析", "description": "交需求"},
    {"name": "实现", "description": "交代码"},
]


def test_items_written_before_the_stages_belong_to_the_first_stage(api_client, board):
    """芝士 writes the items in one call and the stages in a later one: the
    items, which name no stage, are the first stage's, in the draft and in the
    published 题目 alike."""
    first = _cheese_writes(
        api_client,
        board,
        space=board["space"]["id"],
        name="分阶段的实验",
        description="## 要求\n\n写一个缓存模拟器。",
        difficulty="中级",
        form="个人",
        submission=[{"name": "代码", "kind": "文件"}],
        rubric=[{"item": "正确", "points": 60}],
    )
    second = _cheese_writes(api_client, board, draft=first["draft"], stages=TWO_STAGES)
    assert second["missing"] == []

    published = _cheese_publishes(api_client, board, first["draft"])
    assert published.status_code == 200, published.text
    task = _read(api_client, board, published.json()["data"]["task"]["id"])
    need = task["stages"][0]["id"]
    assert [e["stage"] for e in task["submissionSchema"]] == [need]
    assert [r["stage"] for r in task["rubric"]] == [need]


def test_splitting_a_published_task_into_stages_keeps_its_items_and_hand_ins(
    api_client, board
):
    """A published task without stages, split by 芝士 writing only the stages:
    its items and what was handed in are the first stage's."""
    task_id = _publish(
        api_client,
        board,
        stages=[],
        submissionSchema=[{"prompt": "代码", "type": "TEXT"}],
        rubric=[{"item": "正确", "points": 60, "criteria": ""}],
    )
    membership_id = _claim(api_client, board, task_id)
    _id(_submit(api_client, board, task_id, membership_id, None, ["hello"]))

    written = _cheese_writes(api_client, board, task=task_id, stages=TWO_STAGES)
    assert written["missing"] == []
    saved = _cheese_publishes(api_client, board, written["draft"])
    assert saved.status_code == 200, saved.text

    task = _read(api_client, board, task_id)
    need = task["stages"][0]["id"]
    assert [e["stage"] for e in task["submissionSchema"]] == [need]
    assert [r["stage"] for r in task["rubric"]] == [need]
    assert _progress(api_client, board["student"], task_id)["mine"]["stages"] == [
        "PENDING_REVIEW",
        "NOT_SUBMITTED",
    ]


def test_stages_restated_without_their_ids_stay_the_same_stages(api_client, board):
    """芝士 writes the stages again by name only, in another order: each is
    the stage of that name, so its id, items, rubric and hand-ins stay with it."""
    task_id = _publish(api_client, board)
    membership_id = _claim(api_client, board, task_id)
    _id(_submit(api_client, board, task_id, membership_id, NEED, ["需求"]))

    written = _cheese_writes(
        api_client,
        board,
        task=task_id,
        stages=[{"name": "实现"}, {"name": "需求分析"}],
    )
    assert written["missing"] == []
    saved = _cheese_publishes(api_client, board, written["draft"])
    assert saved.status_code == 200, saved.text

    task = _read(api_client, board, task_id)
    assert [(s["id"], s["name"]) for s in task["stages"]] == [
        (IMPL, "实现"),
        (NEED, "需求分析"),
    ]
    assert {r["item"]: r["stage"] for r in task["rubric"]} == {
        "完整": NEED,
        "正确": IMPL,
    }
    assert _progress(api_client, board["student"], task_id)["mine"]["stages"] == [
        "NOT_SUBMITTED",
        "PENDING_REVIEW",
    ]


def test_cheese_removing_every_stage_keeps_the_items_and_rubric(api_client, board):
    """芝士 writes no stages onto a staged task: as on the page, the items and
    rubric lines stay, on the one stage a task without stages has."""
    task_id = _publish(api_client, board)
    written = _cheese_writes(api_client, board, task=task_id, stages=[])
    assert written["removed"] == []
    assert written["missing"] == []
    saved = _cheese_publishes(api_client, board, written["draft"])
    assert saved.status_code == 200, saved.text

    task = _read(api_client, board, task_id)
    assert task["stages"] == []
    assert [(e["prompt"], e["stage"]) for e in task["submissionSchema"]] == [
        ("需求文档", None),
        ("代码", None),
        ("说明", None),
    ]
    assert [(r["item"], r["stage"]) for r in task["rubric"]] == [
        ("完整", None),
        ("正确", None),
    ]


def test_the_form_removing_every_stage_keeps_the_items_and_rubric(api_client, board):
    """The same through the edit form's save, which sends the stages alone."""
    task_id = _publish(api_client, board)
    resp = api_client.patch(
        f"/tasks/{task_id}", json={"stages": []}, headers=_headers(board["teacher"])
    )
    assert resp.status_code == 200, resp.text
    task = _read(api_client, board, task_id)
    assert task["stages"] == []
    assert len(task["submissionSchema"]) == 3
    assert len(task["rubric"]) == 2


def test_stages_rewritten_under_new_names_say_what_left_with_them(api_client, board):
    """芝士 rewrites the stages under new names and no ids: those are new
    stages, the old ones are removed, and what belonged to them is named in
    the write's answer and on the person's card. A draft left with nothing to
    hand in is not ready to save."""
    task_id = _publish(api_client, board)
    written = _cheese_writes(
        api_client,
        board,
        task=task_id,
        stages=[{"name": "设计"}, {"name": "编码"}],
    )
    assert sorted(written["removed"]) == sorted(
        ["需求文档", "代码", "说明", "完整", "正确"]
    )
    assert "submission" in written["missing"]
    saved = _cheese_publishes(api_client, board, written["draft"])
    assert saved.status_code == 400, saved.text
    # Nothing was saved onto the 题目.
    assert len(_read(api_client, board, task_id)["submissionSchema"]) == 3


def _passed_and_rejected(api_client, board):
    """A claim with the first stage accepted and the second sent back."""
    task_id = _publish(api_client, board)
    membership_id = _claim(api_client, board, task_id)
    first = _submit(api_client, board, task_id, membership_id, NEED, ["需求"])
    _review(api_client, board, task_id, membership_id, _id(first), True)
    second = _submit(api_client, board, task_id, membership_id, IMPL, ["代码", "说明"])
    _review(api_client, board, task_id, membership_id, _id(second), False)
    return task_id, membership_id


def test_removing_every_stage_never_merges_the_hand_ins(api_client, board):
    """Removing all the stages would put the second stage's sent-back hand-in
    on one stage with the first's accepted one. Hand-ins never move between
    stages, so a stage holding one cannot go — however many go with it — on
    the form's save and through 芝士 alike."""
    task_id, membership_id = _passed_and_rejected(api_client, board)

    patched = api_client.patch(
        f"/tasks/{task_id}", json={"stages": []}, headers=_headers(board["teacher"])
    )
    assert patched.status_code == 400, patched.text
    assert patched.json()["error"]["i18n"]["key"] == "stageHasSubmissions"
    written = _cheese_writes(api_client, board, task=task_id, stages=[])
    saved = _cheese_publishes(api_client, board, written["draft"])
    assert saved.status_code == 400, saved.text

    assert len(_read(api_client, board, task_id)["stages"]) == 2
    assert _completion(api_client, board, task_id, membership_id) == (
        "REJECTED_RESUBMITTABLE"
    )


def test_removing_every_stage_keeps_the_first_stages_hand_ins(api_client, board):
    """With hand-ins in the first stage only, the stages can all go: the first
    stage is the task's one stage, its hand-in stays, and every item stays."""
    task_id = _publish(api_client, board)
    membership_id = _claim(api_client, board, task_id)
    _id(_submit(api_client, board, task_id, membership_id, NEED, ["需求"]))

    patched = api_client.patch(
        f"/tasks/{task_id}", json={"stages": []}, headers=_headers(board["teacher"])
    )
    assert patched.status_code == 200, patched.text
    task = _read(api_client, board, task_id)
    assert len(task["submissionSchema"]) == 3
    assert _progress(api_client, board["student"], task_id)["mine"]["stages"] == [
        "PENDING_REVIEW"
    ]


def test_cheese_renames_a_task_that_has_no_submission_items(api_client, board):
    """A 题目 published without items stays editable: renaming it is not taking
    away anything it had."""
    task_id = _publish(api_client, board, stages=[], submissionSchema=[], rubric=[])
    written = _cheese_writes(api_client, board, task=task_id, name="改个名字")
    assert written["missing"] == []
    saved = _cheese_publishes(api_client, board, written["draft"])
    assert saved.status_code == 200, saved.text
    assert _read(api_client, board, task_id)["name"] == "改个名字"


def test_the_edit_page_cannot_save_away_every_submission_item(api_client, board):
    """The edit page's own save refuses what 芝士's does: a change draft that
    takes away every item the 题目 has."""
    task_id = _publish(api_client, board, stages=[], rubric=[])
    teacher = _headers(board["teacher"])
    draft = api_client.post("/task-drafts", json={"task": task_id}, headers=teacher)
    assert draft.status_code in (200, 201), draft.text
    draft_id = draft.json()["data"]["id"]
    api_client.patch(
        f"/task-drafts/{draft_id}",
        json={"fields": {"submissionSchema": []}},
        headers=teacher,
    )
    saved = api_client.post(f"/task-drafts/{draft_id}/publish", headers=teacher)
    assert saved.status_code == 400, saved.text
    assert len(_read(api_client, board, task_id)["submissionSchema"]) == 3


def test_a_stage_edit_is_a_new_version_of_the_task(api_client, board):
    """Changing a stage changes what a taker is asked for: the 题目 records a new
    version that says its stages changed, and the brief shows the new stage."""
    task_id = _publish(api_client, board)
    _claim(api_client, board, task_id)
    student = _headers(board["student"])
    # 开始做这道题：从题目来的项目就是这样开的。
    resp = api_client.post(
        f"/tasks/{task_id}/participations/user", json={}, headers=student
    )
    assert resp.status_code == 200, resp.text
    project_id = resp.json()["data"]["project"]["id"]

    first, second = _read(api_client, board, task_id)["stages"]
    resp = api_client.patch(
        f"/tasks/{task_id}",
        json={"stages": [first, {**second, "name": "编码实现", "dueAt": _ms(7)}]},
        headers=_headers(board["teacher"]),
    )
    assert resp.status_code == 200, resp.text

    progress = api_client.get(f"/projects/{project_id}/progress", headers=student)
    assert progress.status_code == 200, progress.text
    revised = [
        (item["version"], item["changes"])
        for item in progress.json()["data"]["data"]
        if item["kind"] == "revised"
    ]
    assert revised == [(2, {"stages": True})]
    brief = api_client.get(
        f"/projects/{project_id}/library/text",
        params={"path": f"题目/{package.BRIEF}"},
        headers=student,
    )
    assert brief.status_code == 200, brief.text
    content = brief.json()["data"]["content"]
    assert "第 2 版" in content
    assert "## 阶段" in content
    assert "编码实现" in content


def _changes_heard(api_client, user) -> list[str]:
    resp = api_client.get(
        "/notifications", params={"pageSize": 50}, headers=_headers(user)
    )
    assert resp.status_code == 200, resp.text
    return [
        n["contextMetadata"]["content"]
        for n in resp.json()["data"]["notifications"]
        if n["type"] == "TASK_NOTICE"
        and n["contextMetadata"].get("event") == "requirementsChanged"
    ]


# 2030-11-15 15:59 UTC: 23:59 in Beijing, 10:59 in New York.
DUE = int(datetime(2030, 11, 15, 15, 59, tzinfo=UTC).timestamp() * 1000)
LATE_DEADLINE = int(datetime(2031, 1, 1, tzinfo=UTC).timestamp() * 1000)


def test_reordering_stages_alone_tells_takers_nothing(api_client, board):
    """A new order asks nothing new of whoever took the 题目."""
    task_id = _publish(api_client, board)
    _claim(api_client, board, task_id)
    task = _read(api_client, board, task_id)
    first, second = task["stages"]
    resp = api_client.patch(
        f"/tasks/{task_id}",
        json={
            "stages": [second, first],
            "submissionSchema": task["submissionSchema"],
            "rubric": task["rubric"],
        },
        headers=_headers(board["teacher"]),
    )
    assert resp.status_code == 200, resp.text
    assert _changes_heard(api_client, board["student"]) == []


def test_a_renamed_stage_is_told_with_its_old_and_new_name(api_client, board):
    """Takers look for a stage by its name, so a new one reaches them, saying
    which stage it was and what it is called now; moved in the same save, it is
    still the one notice."""
    task_id = _publish(api_client, board)
    _claim(api_client, board, task_id)
    task = _read(api_client, board, task_id)
    first, second = task["stages"]
    resp = api_client.patch(
        f"/tasks/{task_id}",
        json={
            "stages": [{**second, "name": "编码实现"}, first],
            "submissionSchema": task["submissionSchema"],
            "rubric": task["rubric"],
        },
        headers=_headers(board["teacher"]),
    )
    assert resp.status_code == 200, resp.text
    assert _changes_heard(api_client, board["student"]) == [
        f"「{task['name']}」阶段「实现」改名为「编码实现」"
    ]


def test_a_renamed_task_is_told_by_the_name_its_takers_knew(api_client, board):
    """The notice names the 题目 as its takers knew it and says its new name."""
    task_id = _publish(api_client, board)
    _claim(api_client, board, task_id)
    old_name = _read(api_client, board, task_id)["name"]
    resp = api_client.patch(
        f"/tasks/{task_id}",
        json={"name": "B 树的插入与删除"},
        headers=_headers(board["teacher"]),
    )
    assert resp.status_code == 200, resp.text
    assert _changes_heard(api_client, board["student"]) == [
        f"「{old_name}」改名为「B 树的插入与删除」"
    ]


def test_a_moved_stage_due_date_is_told_on_the_readers_clock(api_client, board):
    """Takers hear which stage's due date moved and to when, on their own
    clock: Beijing time until their browser says otherwise."""
    task_id = _publish(api_client, board, deadline=LATE_DEADLINE)
    _claim(api_client, board, task_id)

    def move(due: int) -> None:
        first, second = _read(api_client, board, task_id)["stages"]
        resp = api_client.patch(
            f"/tasks/{task_id}",
            json={"stages": [first, {**second, "dueAt": due}]},
            headers=_headers(board["teacher"]),
        )
        assert resp.status_code == 200, resp.text

    move(DUE)
    [heard] = _changes_heard(api_client, board["student"])
    assert heard.endswith("阶段「实现」的截止时间改为 2030-11-15 23:59（北京时间）")

    resp = api_client.put(
        "/users/me/timezone",
        json={"timezone": "America/New_York"},
        headers=_headers(board["student"]),
    )
    assert resp.status_code == 200, resp.text
    move(DUE + DAY_MS)
    latest = _changes_heard(api_client, board["student"])[0]
    assert latest.endswith(
        "阶段「实现」的截止时间改为 2030-11-16 10:59（America/New_York）"
    )


def test_several_changes_in_one_save_are_one_notice(api_client, board):
    """A stage added and the rubric changed in the same save: one notice that
    names both."""
    task_id = _publish(api_client, board)
    _claim(api_client, board, task_id)
    first, second = _read(api_client, board, task_id)["stages"]
    resp = api_client.patch(
        f"/tasks/{task_id}",
        json={
            "stages": [
                first,
                second,
                {"name": "答辩", "description": "", "dueAt": None},
            ],
            "rubric": [{**RUBRIC[0], "points": 50}, {**RUBRIC[1], "points": 50}],
        },
        headers=_headers(board["teacher"]),
    )
    assert resp.status_code == 200, resp.text
    [heard] = _changes_heard(api_client, board["student"])
    assert "修改了提交内容或评分要点" in heard
    assert "新增了阶段「答辩」" in heard


def test_a_stage_missed_with_nothing_handed_in_reads_failed_to_its_claim(
    api_client, board
):
    """需求分析 was due yesterday and nothing was handed in for it, so the claim
    has failed for it: the student reads that stage as failed, not as not yet
    handed in, while the stage still open reads not handed in."""
    task_id = _publish(
        api_client, board, stages=[{**STAGES[0], "dueAt": _ms(-1)}, STAGES[1]]
    )
    _claim(api_client, board, task_id)

    mine = _progress(api_client, board["student"], task_id)["mine"]
    assert mine["stages"] == ["MISSED", "NOT_SUBMITTED"]


def test_the_publisher_hears_who_a_stage_change_fails_or_reopens(api_client, board):
    """Before saving, the publisher asks what the stages they are about to save
    would do: moving an empty stage's due date into the past fails the claim,
    moving it back takes the claim out of failed. The answer names the
    participants and the stage they would miss, not only how many. Asking
    writes nothing, and only whoever may change the task may ask."""
    task_id = _publish(api_client, board)
    membership_id = _claim(api_client, board, task_id)
    stages = _read(api_client, board, task_id)["stages"]
    overdue = [{**stages[0], "dueAt": _ms(-1)}, stages[1]]

    def effect(proposed, user=board["teacher"]):
        return api_client.post(
            f"/tasks/{task_id}/stages/effect",
            json={"stages": proposed},
            headers=_headers(user),
        )

    student = board["student"].nickname
    asked = effect(overdue)
    assert asked.status_code == 200, asked.text
    assert asked.json()["data"] == {
        "failing": 1,
        "reopening": 0,
        "failingNames": [student],
        "reopeningNames": [],
        "failingStages": ["需求分析"],
    }
    assert _completion(api_client, board, task_id, membership_id) == "NOT_SUBMITTED"
    unchanged = effect(stages).json()["data"]
    assert (unchanged["failing"], unchanged["reopening"]) == (0, 0)

    saved = api_client.patch(
        f"/tasks/{task_id}",
        json={"stages": overdue},
        headers=_headers(board["teacher"]),
    )
    assert saved.status_code == 200, saved.text
    assert _completion(api_client, board, task_id, membership_id) == "FAILED"
    back = effect(stages).json()["data"]
    assert (back["failing"], back["reopening"]) == (0, 1)
    assert back["reopeningNames"] == [student]

    assert effect(stages, board["student"]).status_code == 403


def test_the_stage_change_preview_refuses_what_the_save_refuses(api_client, board):
    """需求分析 holds a handed-in version that was sent back, and is past due, so
    the claim has failed. Dropping that stage is refused by the save; asking
    what the drop would do gets the same refusal, never a promise that the
    claim goes back to in progress."""
    task_id = _publish(api_client, board)
    membership_id = _claim(api_client, board, task_id)
    returned = _submit(api_client, board, task_id, membership_id, NEED, ["需求"])
    _review(api_client, board, task_id, membership_id, _id(returned), False)
    need, impl = _read(api_client, board, task_id)["stages"]
    overdue = api_client.patch(
        f"/tasks/{task_id}",
        json={"stages": [{**need, "dueAt": _ms(-1)}, impl]},
        headers=_headers(board["teacher"]),
    )
    assert overdue.status_code == 200, overdue.text
    assert _completion(api_client, board, task_id, membership_id) == "FAILED"

    asked = api_client.post(
        f"/tasks/{task_id}/stages/effect",
        json={"stages": [impl]},
        headers=_headers(board["teacher"]),
    )
    saved = api_client.patch(
        f"/tasks/{task_id}",
        json={"stages": [impl]},
        headers=_headers(board["teacher"]),
    )

    assert saved.status_code == 400, saved.text
    assert asked.status_code == 400, asked.text
    assert asked.json()["error"]["i18n"] == saved.json()["error"]["i18n"]
    assert asked.json()["error"]["i18n"]["key"] == "stageHasSubmissions"


def test_cheese_writing_stages_hears_who_saving_them_would_fail(api_client, board):
    """芝士 moves an empty stage's due date into the past in the task's changes:
    before anything is saved, its answer and the person's change card say one
    participant would be marked failed, as the edit page would."""
    task_id = _publish(api_client, board)
    _claim(api_client, board, task_id)
    need, impl = _read(api_client, board, task_id)["stages"]

    written = api_client.post(
        "/task-drafts/agent/write",
        json={
            "task": task_id,
            "stages": [{**_as_written(need), "due": "2020-01-01"}, _as_written(impl)],
        },
        headers=_as_cheese(board["teacher"]),
    )

    assert written.status_code == 200, written.text
    assert written.json()["data"]["stageEffect"] == (
        "1 位参与者有已过截止、还没提交的阶段，保存后将判为未通过"
    )
    assert [s["dueAt"] for s in _read(api_client, board, task_id)["stages"]] == [
        None,
        None,
    ]


def test_the_publisher_counts_a_missed_stage_as_failed_like_the_student_reads_it(
    api_client, board
):
    """The student reads 需求分析 as failed once it is past due with nothing
    handed in; the publisher's per-stage counts put them there too, not under
    not yet handed in."""
    task_id = _publish(
        api_client, board, stages=[{**STAGES[0], "dueAt": _ms(-1)}, STAGES[1]]
    )
    _claim(api_client, board, task_id)

    first, second = _progress(api_client, board["teacher"], task_id)["distribution"]
    assert (first["MISSED"], first["NOT_SUBMITTED"]) == (1, 0)
    assert (second["MISSED"], second["NOT_SUBMITTED"]) == (0, 1)


def test_cheese_hears_no_counts_for_someone_who_may_no_longer_change_the_task(
    api_client, board, db_session, _portal
):
    """A change draft outlives its writer's right to change the task: once the
    writer is neither its publisher nor an admin of its space, 芝士 writing
    stages into that draft hears nothing about the task's takers."""
    task_id = _publish(api_client, board)
    _claim(api_client, board, task_id)
    need, impl = _read(api_client, board, task_id)["stages"]
    teacher = board["teacher"]

    def write(target: dict, due: str):
        return api_client.post(
            "/task-drafts/agent/write",
            json={
                **target,
                "stages": [{**_as_written(need), "due": due}, _as_written(impl)],
            },
            headers=_as_cheese(teacher),
        )

    first = write({"task": task_id}, "2020-01-01")
    assert first.status_code == 200, first.text
    assert "stageEffect" in first.json()["data"]

    async def revoke():
        await db_session.execute(
            update(Task)
            .where(Task.id == task_id)
            .values(creator_id=board["student"].user_id)
        )
        await db_session.execute(
            delete(SpaceAdminRelation).where(
                SpaceAdminRelation.space_id == board["space"]["id"],
                SpaceAdminRelation.user_id == teacher.user_id,
            )
        )
        await db_session.flush()

    _portal.call(revoke)
    db_session.expire_all()
    again = write({"draft": first.json()["data"]["draft"]}, "2020-01-02")

    assert again.status_code == 200, again.text
    assert "stageEffect" not in again.json()["data"]


def test_cheese_hears_the_drafts_stage_effect_on_a_write_that_changes_something_else(
    api_client, board
):
    """The effect describes the draft as it stands: after 芝士 moved a stage's
    due date into the past, renaming the task in the same draft still tells
    that saving it would fail one participant; once the stages are the task's
    own again, a write says nothing would."""
    task_id = _publish(api_client, board)
    _claim(api_client, board, task_id)
    need, impl = _read(api_client, board, task_id)["stages"]
    teacher = _as_cheese(board["teacher"])

    moved = api_client.post(
        "/task-drafts/agent/write",
        json={
            "task": task_id,
            "stages": [{**_as_written(need), "due": "2020-01-01"}, _as_written(impl)],
        },
        headers=teacher,
    )
    assert moved.status_code == 200, moved.text
    draft = moved.json()["data"]["draft"]

    renamed = api_client.post(
        "/task-drafts/agent/write",
        json={"draft": draft, "name": "改过名字的实验"},
        headers=teacher,
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["data"]["changed"] == ["name"]
    assert renamed.json()["data"]["stageEffect"] == (
        "1 位参与者有已过截止、还没提交的阶段，保存后将判为未通过"
    )

    restored = api_client.post(
        "/task-drafts/agent/write",
        json={"draft": draft, "stages": [_as_written(need), _as_written(impl)]},
        headers=teacher,
    )
    assert restored.status_code == 200, restored.text
    assert "stageEffect" not in restored.json()["data"]
