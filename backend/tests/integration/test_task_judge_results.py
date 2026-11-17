"""读一版提交的评测结果与一个用例（方案 §看结果）。

第 1 步还没有东西产出评测结果（队列、执行器、计费在 1b），所以这里写一行
``task_judge_run`` 当作结果，然后走真 HTTP 读它：

* ``GET .../submissions/{sid}/judge`` —— 谁都能看状态和「通过几个 / 一共几个」；逐个
  用例的输入、期望输出、实际输出只给能批改的人，学生拿到的是名称、过没过和时间；
* ``GET .../judge/cases/{caseId}`` —— 一个用例的输入与期望输出，**只给能批改的人**；
* 评测不是评审：只出了评测结果时，这一版仍显示待批改（``test_task_review_line_scores``
  里另有一条钉这句，这里既然有结果就一并读）。
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.domain.task.judging_models import TaskJudgeRun, TaskTestCase, TaskTestSuite
from tests.integration.conftest import UserCreator
from tests.integration.test_task_files import (
    _auth,
    _create_task,
    _member_of,
    _new_board,
    upload_root,  # noqa: F401 — 建题要的那个夹具
)

SUITE = [
    {
        "kind": "IO",
        "runtime": "python3.12",
        "runCmd": "python3 main.py",
        "cases": [
            {
                "name": "公开的样例",
                "points": 60,
                "visibility": "SAMPLE",
                "input": "1 2\n",
                "expected": "3\n",
            },
            {
                "name": "隐藏的边界",
                "points": 40,
                "visibility": "HIDDEN",
                "input": "100 200\n",
                "expected": "300\n",
            },
        ],
    }
]

#: 一行评测结果里的逐个用例：和方案 §数据结构 说的 ``results`` 同形。
RESULTS = [
    {
        "index": 0,
        "name": "公开的样例",
        "visibility": "SAMPLE",
        "status": "ok",
        "points": 60,
        "ms": 12,
        "input": "1 2\n",
        "expected": "3\n",
        "actual": "3\n",
    },
    {
        "index": 1,
        "name": "隐藏的边界",
        "visibility": "HIDDEN",
        "status": "wrong",
        "points": 40,
        "ms": 15,
        "input": "100 200\n",
        "expected": "300\n",
        "actual": "500\n",
    },
]


def _setup(api_client: TestClient, user_client: UserCreator):
    board = _new_board(user_client, api_client)
    task_id = _create_task(api_client, board, name="读评测结果", testSuite=SUITE)
    _, token = _member_of(user_client, api_client, board)
    started = api_client.post(
        f"/tasks/{task_id}/participations/user", json={}, headers=_auth(token)
    )
    assert started.status_code == 200, started.text
    participant = started.json()["data"]["participant"]["id"]
    submitted = api_client.post(
        f"/tasks/{task_id}/participants/{participant}/submissions",
        json=[{"text": "我的作业"}],
        headers=_auth(token),
    )
    assert submitted.status_code == 200, submitted.text
    submission_id = submitted.json()["data"]["submission"]["id"]
    return board, task_id, participant, submission_id, token


def _seed_run(db_session, _portal, *, task_id: int, submission_id: int) -> None:
    async def _seed() -> None:
        suite_id = await db_session.scalar(
            select(TaskTestSuite.id).where(TaskTestSuite.task_id == task_id)
        )
        assert suite_id is not None
        db_session.add(
            TaskJudgeRun(
                task_id=task_id,
                suite_id=suite_id,
                suite_revision=1,
                submission_id=submission_id,
                purpose="SUBMISSION",
                code_sha="deadbeef",
                state="done",
                passed_points=60,
                total_points=100,
                results=RESULTS,
                created_at=datetime.now(UTC),
            )
        )
        await db_session.flush()

    _portal.call(_seed)


def _judge(
    api_client: TestClient, task_id: int, participant: int, submission: int, token
):
    return api_client.get(
        f"/tasks/{task_id}/participants/{participant}/submissions/{submission}/judge",
        headers=_auth(token),
    )


def test_a_student_sees_states_and_a_hidden_case_s_name_but_not_its_bytes(
    api_client: TestClient, user_client: UserCreator, db_session, _portal
):
    board, task_id, participant, submission_id, token = _setup(api_client, user_client)
    _seed_run(db_session, _portal, task_id=task_id, submission_id=submission_id)

    resp = _judge(api_client, task_id, participant, submission_id, token)
    assert resp.status_code == 200, resp.text
    run = resp.json()["data"]["judge"]
    assert run["state"] == "done"
    assert run["passedPoints"] == 60
    assert run["totalPoints"] == 100
    assert run["passedCases"] == 1
    assert run["caseCount"] == 2
    by_name = {c["name"]: c for c in run["cases"]}
    # 样例用例本来就是公开的：实际输出也在。
    assert by_name["公开的样例"]["actual"] == "3\n"
    hidden = by_name["隐藏的边界"]
    assert hidden["status"] == "wrong"
    # 隐藏用例：输入、期望、实际那三格都不在学生的响应里。
    assert "input" not in hidden
    assert "expected" not in hidden
    assert "actual" not in hidden


def test_a_grader_sees_every_case_s_bytes(
    api_client: TestClient, user_client: UserCreator, db_session, _portal
):
    board, task_id, participant, submission_id, _ = _setup(api_client, user_client)
    _seed_run(db_session, _portal, task_id=task_id, submission_id=submission_id)

    resp = _judge(
        api_client, task_id, participant, submission_id, board["creator_token"]
    )
    assert resp.status_code == 200, resp.text
    run = resp.json()["data"]["judge"]
    hidden = next(c for c in run["cases"] if c["visibility"] == "HIDDEN")
    assert hidden["input"] == "100 200\n"
    assert hidden["expected"] == "300\n"
    assert hidden["actual"] == "500\n"


def test_a_grader_reads_one_case(
    api_client: TestClient, user_client: UserCreator, db_session, _portal
):
    board, task_id, participant, submission_id, _ = _setup(api_client, user_client)
    _seed_run(db_session, _portal, task_id=task_id, submission_id=submission_id)

    async def _case_id() -> int:
        suite_id = await db_session.scalar(
            select(TaskTestSuite.id).where(TaskTestSuite.task_id == task_id)
        )
        return await db_session.scalar(
            select(TaskTestCase.id).where(
                TaskTestCase.suite_id == suite_id, TaskTestCase.name == "隐藏的边界"
            )
        )

    case_id = _portal.call(_case_id)
    resp = api_client.get(
        f"/tasks/{task_id}/judge/cases/{case_id}",
        headers=_auth(board["creator_token"]),
    )
    assert resp.status_code == 200, resp.text
    case = resp.json()["data"]["case"]
    assert case["name"] == "隐藏的边界"
    assert case["input"] == "100 200\n"
    assert case["expected"] == "300\n"


def test_one_case_is_only_for_who_may_grade(
    api_client: TestClient, user_client: UserCreator, db_session, _portal
):
    board, task_id, participant, submission_id, token = _setup(api_client, user_client)

    async def _case_id() -> int:
        suite_id = await db_session.scalar(
            select(TaskTestSuite.id).where(TaskTestSuite.task_id == task_id)
        )
        return await db_session.scalar(
            select(TaskTestCase.id).where(TaskTestCase.suite_id == suite_id)
        )

    case_id = _portal.call(_case_id)
    # 学生（领题的人）读不到这一个用例的字节。
    resp = api_client.get(
        f"/tasks/{task_id}/judge/cases/{case_id}", headers=_auth(token)
    )
    assert resp.status_code == 403, resp.text


def test_a_judge_result_of_another_submission_is_not_shown(
    api_client: TestClient, user_client: UserCreator, db_session, _portal
):
    """别的提交的评测结果不在这一版上：读的是这个问题里这一版的那一次。"""
    board, task_id, participant, submission_id, token = _setup(api_client, user_client)
    resp = _judge(api_client, task_id, participant, submission_id, token)
    assert resp.status_code == 200, resp.text
    # 没有评测过：null，而不是别人的。
    assert resp.json()["data"]["judge"] is None


def test_a_case_of_another_task_is_not_this_task_s(
    api_client: TestClient, user_client: UserCreator, db_session, _portal
):
    board = _new_board(user_client, api_client)
    mine = _create_task(api_client, board, name="我的题", testSuite=SUITE)
    other = _create_task(api_client, board, name="别人的题", testSuite=SUITE)

    async def _case_id() -> int:
        suite_id = await db_session.scalar(
            select(TaskTestSuite.id).where(TaskTestSuite.task_id == other)
        )
        return await db_session.scalar(
            select(TaskTestCase.id).where(TaskTestCase.suite_id == suite_id)
        )

    case_id = _portal.call(_case_id)
    resp = api_client.get(
        f"/tasks/{mine}/judge/cases/{case_id}", headers=_auth(board["creator_token"])
    )
    assert resp.status_code == 404, resp.text
