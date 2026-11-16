"""批改时逐项打分（方案 §评审那一行、§7 分数怎么合成）。

一道挂了测试点的题，评分要点里有一项设为「测试点」。老师批改时只给其余项打分，测试
那一项由后端按这一版提交的评测结果折算，总分由各项合成，逐项得分随评审一起存下、读
回来。测试那一项**不自动判通过**：没有评测结果时它空着，老师可以手动填并写原因（学生
看得到）。

这里的评测结果由测试直接写一行 ``task_judge_run``（第 1 步没有东西产出它，队列在
1b）；走的是真 HTTP：提交、批改、读回。

例子都取自方案那张表：

* 60（测试点）/20/20，用例分 70/100 → 42、20、18，总分 80；
* 老师把测试那一项手动调到 48，写明原因 → 20、18、总分 86。
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.domain.task.judging_models import TaskJudgeRun, TaskTestSuite
from tests.integration.conftest import UserCreator
from tests.integration.test_task_files import (
    _auth,
    _create_task,
    _member_of,
    _new_board,
    upload_root,  # noqa: F401 — 建题要的那个夹具
)

RUBRIC = [
    {"item": "正确性", "points": 60, "criteria": "全对", "source": "TESTS"},
    {"item": "实现", "points": 20, "criteria": "LRU 对"},
    {"item": "报告", "points": 20, "criteria": ""},
]

SUITE = [
    {
        "kind": "IO",
        "runtime": "python3.12",
        "runCmd": "python3 main.py",
        "cases": [
            {
                "name": "a",
                "points": 70,
                "visibility": "SAMPLE",
                "input": "1",
                "expected": "1",
            },
            {
                "name": "b",
                "points": 30,
                "visibility": "HIDDEN",
                "input": "2",
                "expected": "2",
            },
        ],
    }
]


def _setup(api_client: TestClient, user_client: UserCreator):
    """一块板、一道挂了测试点的题、一个交了一版的学生。"""
    board = _new_board(user_client, api_client)
    task_id = _create_task(
        api_client, board, name="逐项打分", rubric=RUBRIC, testSuite=SUITE
    )
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


def _seed_run(
    db_session, _portal, *, task_id: int, submission_id: int, passed: int, total: int
) -> None:
    """写一行评测结果（第 1 步没有东西产出它，队列在 1b）。"""

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
                state="done",
                passed_points=passed,
                total_points=total,
                results=[],
                created_at=datetime.now(UTC),
            )
        )
        await db_session.flush()

    _portal.call(_seed)


def _review(
    api_client: TestClient,
    task_id: int,
    participant: int,
    submission_id: int,
    token: str,
    **body,
):
    return api_client.post(
        f"/tasks/{task_id}/participants/{participant}/submissions/"
        f"{submission_id}/review",
        json=body,
        headers=_auth(token),
    )


def test_the_tests_line_is_filled_in_and_the_total_is_composed(
    api_client: TestClient, user_client: UserCreator, db_session, _portal
):
    board, task_id, participant, submission_id, _ = _setup(api_client, user_client)
    _seed_run(
        db_session,
        _portal,
        task_id=task_id,
        submission_id=submission_id,
        passed=70,
        total=100,
    )

    resp = _review(
        api_client,
        task_id,
        participant,
        submission_id,
        board["creator_token"],
        accepted=True,
        comment="好",
        lineScores=[{"index": 1, "score": 20}, {"index": 2, "score": 18}],
    )
    assert resp.status_code == 200, resp.text
    detail = resp.json()["data"]["review"]["detail"]
    assert detail["score"] == 80
    lines = {entry["index"]: entry for entry in detail["lineScores"]}
    assert lines[0]["score"] == 42
    assert lines[0]["source"] == "TESTS"
    assert lines[1]["score"] == 20
    assert lines[2]["score"] == 18


def test_a_manual_adjust_needs_a_reason_and_the_total_follows(
    api_client: TestClient, user_client: UserCreator, db_session, _portal
):
    board, task_id, participant, submission_id, _ = _setup(api_client, user_client)
    _seed_run(
        db_session,
        _portal,
        task_id=task_id,
        submission_id=submission_id,
        passed=70,
        total=100,
    )

    resp = _review(
        api_client,
        task_id,
        participant,
        submission_id,
        board["creator_token"],
        accepted=True,
        comment="好",
        lineScores=[
            {"index": 0, "score": 48, "adjusted": True, "note": "手算的"},
            {"index": 1, "score": 20},
            {"index": 2, "score": 18},
        ],
    )
    assert resp.status_code == 200, resp.text
    detail = resp.json()["data"]["review"]["detail"]
    assert detail["score"] == 86
    lines = {entry["index"]: entry for entry in detail["lineScores"]}
    assert lines[0]["score"] == 48
    assert lines[0]["adjusted"] is True
    assert lines[0]["note"] == "手算的"


def test_a_manual_tests_score_without_a_reason_is_refused(
    api_client: TestClient, user_client: UserCreator
):
    board, task_id, participant, submission_id, _ = _setup(api_client, user_client)
    resp = _review(
        api_client,
        task_id,
        participant,
        submission_id,
        board["creator_token"],
        accepted=True,
        comment="好",
        lineScores=[{"index": 0, "score": 48}],
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["error"]["i18n"]["key"] == "lineScoreTestsManual"


def test_without_a_run_the_tests_line_stays_blank(
    api_client: TestClient, user_client: UserCreator
):
    """评测还没出结果时，测试那一项空着，老师照常批改 —— 它两边都不计。"""
    board, task_id, participant, submission_id, _ = _setup(api_client, user_client)
    resp = _review(
        api_client,
        task_id,
        participant,
        submission_id,
        board["creator_token"],
        accepted=True,
        comment="好",
        lineScores=[{"index": 1, "score": 20}, {"index": 2, "score": 18}],
    )
    assert resp.status_code == 200, resp.text
    detail = resp.json()["data"]["review"]["detail"]
    lines = {entry["index"]: entry for entry in detail["lineScores"]}
    assert lines[0]["score"] is None
    # 空着的一项得分与分值两边都不计：(20 + 18) / (20 + 20) × 100 = 95。若把它当成 0
    # 分、分值仍计入，会是 38/100×100 = 38 —— 95 这个数正说明它被整项跳过了。
    assert detail["score"] == 95


def test_an_accepted_review_must_score_every_teacher_line(
    api_client: TestClient, user_client: UserCreator, db_session, _portal
):
    board, task_id, participant, submission_id, _ = _setup(api_client, user_client)
    _seed_run(
        db_session,
        _portal,
        task_id=task_id,
        submission_id=submission_id,
        passed=70,
        total=100,
    )
    resp = _review(
        api_client,
        task_id,
        participant,
        submission_id,
        board["creator_token"],
        accepted=True,
        comment="好",
        lineScores=[{"index": 1, "score": 20}],
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["error"]["i18n"]["key"] == "lineScoreMissing"


def test_a_submission_with_only_a_judge_result_is_still_pending_review(
    api_client: TestClient, user_client: UserCreator, db_session, _portal
):
    """只出了评测结果、老师还没批改时，这一版仍显示待批改 —— 评测不是评审。"""
    board, task_id, participant, submission_id, _ = _setup(api_client, user_client)
    _seed_run(
        db_session,
        _portal,
        task_id=task_id,
        submission_id=submission_id,
        passed=70,
        total=100,
    )
    listed = api_client.get(
        f"/tasks/{task_id}/participants/{participant}/submissions",
        params={"queryReview": "true"},
        headers=_auth(board["creator_token"]),
    )
    assert listed.status_code == 200, listed.text
    rows = listed.json()["data"]["submissions"]
    assert len(rows) == 1
    assert rows[0]["review"]["reviewed"] is False
