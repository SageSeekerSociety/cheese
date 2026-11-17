"""批改时测试点那一项怎么给分（方案 §评审那一行、§7 分数怎么合成）。

一道挂了测试点的题，评分要点里有一项设为 ``kind: "tests"``。老师批改时只给手动项打
分（填进 ``score``），测试那一项由后端按这一版提交的评测结果折算，总分 = 老师填的手
动分 + 自动行的分，怎么来的记进 ``auto_scores``（读者看到的是 ``autoScores``）。测试
那一项**不自动判通过**：没有评测结果、或者评测失败时给 0 并标 ``pending``，老师可以
用 ``testsOverride``（带原因）手动给分。

这里的评测结果由测试直接写一行 ``task_judge_run``（第 1 步没有东西产出它，队列在
1b）；走的是真 HTTP：提交、批改、读回。

例子都取自方案那张表：

* 正确性 60（tests）+ 手动 40，用例分 70/100 → 测试 42，老师填 38 → 总分 80；
* 老师把测试那一项手动调到 48，写明原因 → 总分 86；
* 纯编程题一项 100（tests），用例分 85/100 → 总分 85。
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
    {"item": "正确性", "points": 60, "criteria": "全对", "kind": "tests"},
    {"item": "实现", "points": 40, "criteria": "LRU 对"},
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

#: 一阶段里手动 30 + 测试 40 + 额度 30（合计 100，含自动行的那一阶段要凑满）。
USAGE_AND_TESTS = [
    {"item": "实现", "points": 30, "criteria": "LRU 对"},
    {"item": "正确性", "points": 40, "criteria": "全对", "kind": "tests"},
    {
        "item": "额度用量",
        "points": 30,
        "criteria": "",
        "kind": "usage",
        "config": {"mode": "threshold", "full": 50, "zero": 200},
    },
]


def _setup(
    api_client: TestClient, user_client: UserCreator, *, rubric=RUBRIC, suite=SUITE
):
    """一块板、一道挂了测试点的题、一个交了一版的学生。"""
    board = _new_board(user_client, api_client)
    task_id = _create_task(
        api_client, board, name="逐项打分", rubric=rubric, testSuite=suite
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


def _tests_line(detail: dict) -> dict:
    """评审里 tests 那一行（``autoScores.lines`` 里 ``kind == "tests"`` 的那个）。"""
    lines = detail["autoScores"]["lines"]
    return next(entry for entry in lines if entry["kind"] == "tests")


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
        score=38,
    )
    assert resp.status_code == 200, resp.text
    detail = resp.json()["data"]["review"]["detail"]
    assert detail["score"] == 80
    assert detail["autoScores"]["manual"] == 38
    line = _tests_line(detail)
    assert line["points"] == 60
    assert line["score"] == 42
    assert line["passedPoints"] == 70
    assert line["totalPoints"] == 100
    assert line["adjusted"] is False


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
        score=38,
        testsOverride={"score": 48, "reason": "手算的"},
    )
    assert resp.status_code == 200, resp.text
    detail = resp.json()["data"]["review"]["detail"]
    assert detail["score"] == 86
    line = _tests_line(detail)
    assert line["score"] == 48
    assert line["adjusted"] is True
    assert line["reason"] == "手算的"


def test_a_pure_programming_task(
    api_client: TestClient, user_client: UserCreator, db_session, _portal
):
    board, task_id, participant, submission_id, _ = _setup(
        api_client,
        user_client,
        rubric=[{"item": "正确性", "points": 100, "criteria": "全对", "kind": "tests"}],
    )
    _seed_run(
        db_session,
        _portal,
        task_id=task_id,
        submission_id=submission_id,
        passed=85,
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
        score=0,
    )
    assert resp.status_code == 200, resp.text
    detail = resp.json()["data"]["review"]["detail"]
    assert detail["score"] == 85
    assert _tests_line(detail)["score"] == 85


def test_a_manual_tests_score_without_a_reason_is_refused(
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
        score=38,
        testsOverride={"score": 48},
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["error"]["i18n"]["key"] == "lineScoreNeedsReason"


def test_a_manual_tests_score_over_the_line_is_refused(
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
        score=38,
        testsOverride={"score": 61, "reason": "手算的"},
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["error"]["i18n"]["key"] == "lineScoreRange"


def test_without_a_run_the_tests_line_scores_zero_and_is_pending(
    api_client: TestClient, user_client: UserCreator
):
    """评测还没出结果时，测试那一项给 0 并标 pending，老师照常批改。"""
    board, task_id, participant, submission_id, _ = _setup(api_client, user_client)
    resp = _review(
        api_client,
        task_id,
        participant,
        submission_id,
        board["creator_token"],
        accepted=True,
        comment="好",
        score=38,
    )
    assert resp.status_code == 200, resp.text
    detail = resp.json()["data"]["review"]["detail"]
    # 没有结果：tests 行给 0，总分就是老师填的手动分。
    assert detail["score"] == 38
    line = _tests_line(detail)
    assert line["score"] == 0
    assert line["pending"] is True
    # final 只管按排名给分的额度行：tests 行这一刻就定了（0 分），不拖整条评审。
    assert detail["autoScores"]["final"] is True


def test_a_failed_run_scores_zero_and_is_pending(
    api_client: TestClient, user_client: UserCreator, db_session, _portal
):
    """评测失败时，测试那一项同样给 0 并标 pending。"""
    board, task_id, participant, submission_id, _ = _setup(api_client, user_client)

    async def _seed_failed() -> None:
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
                state="failed",
                results=[],
                created_at=datetime.now(UTC),
            )
        )
        await db_session.flush()

    _portal.call(_seed_failed)
    resp = _review(
        api_client,
        task_id,
        participant,
        submission_id,
        board["creator_token"],
        accepted=True,
        comment="好",
        score=38,
    )
    assert resp.status_code == 200, resp.text
    detail = resp.json()["data"]["review"]["detail"]
    assert detail["score"] == 38
    line = _tests_line(detail)
    assert line["score"] == 0
    assert line["pending"] is True


def test_a_manual_score_beyond_the_manual_lines_is_refused(
    api_client: TestClient, user_client: UserCreator, db_session, _portal
):
    """老师填的手动分超过手动项分值和（这里 40）就拒掉。"""
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
        score=41,
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["error"]["i18n"]["key"] == "reviewManualScoreRange"


def test_a_rejected_review_records_no_auto_scores(
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
        accepted=False,
        comment="再来一次",
    )
    assert resp.status_code == 200, resp.text
    detail = resp.json()["data"]["review"]["detail"]
    assert "autoScores" not in detail


def test_a_usage_line_and_a_tests_line_compose_together(
    api_client: TestClient, user_client: UserCreator, db_session, _portal
):
    """额度行和测试行同在一阶段：总分 = 老师的手动分 + 两条自动行的分。"""
    board, task_id, participant, submission_id, _ = _setup(
        api_client, user_client, rubric=USAGE_AND_TESTS
    )
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
        score=30,
    )
    assert resp.status_code == 200, resp.text
    detail = resp.json()["data"]["review"]["detail"]
    # 30（手动）+ 28（测试 40 × 70 ÷ 100）+ 30（额度：这份领题没花过额度，落在满分线内）。
    assert detail["score"] == 88
    assert detail["autoScores"]["manual"] == 30
    lines = {entry["kind"]: entry for entry in detail["autoScores"]["lines"]}
    assert lines["tests"]["score"] == 28
    assert lines["usage"]["score"] == 30


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
