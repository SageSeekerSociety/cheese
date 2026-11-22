"""出题人在「数据」页签看测试点数据，学情分析「题目难度」读测试通过率（方案 §第⑤步）。

评测结果直接写进 ``task_judge_run``（怎么跑出来的在 ``test_judge_dispatch`` 里测），
这里走真 HTTP 读：

* ``GET /tasks/{id}/judge/stats`` —— 每个参与者在这一阶段**最新那一版**提交的最近一次
  评测：每个用例过了几份、没过的最常见是哪种状态，最差的在前；测试分按十档分布；判分
  失败、评测失败、没有评测的另数；这道题评测一共花的额度（提交的评测 + 自测）。只给
  能批改的人；
* ``GET /spaces/{id}/learning-analysis/class/tasks`` —— 挂着测试点的题多一格全班的测试
  平均通过率，不足 3 份不给。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.domain.task.judging_models import TaskJudgeRun, TaskTestSuite
from tests.integration.conftest import UserCreator
from tests.integration.test_task_files import (
    _auth,
    _create_task,
    _member_of,
    _new_board,
    upload_root,  # noqa: F401 — 建题要的那个夹具
)
from tests.integration.test_task_judge_results import SUITE


def _cases(first: str, second: str) -> list[dict]:
    return [
        {"index": 0, "name": "公开的样例", "visibility": "SAMPLE", "status": first},
        {"index": 1, "name": "隐藏的边界", "visibility": "HIDDEN", "status": second},
    ]


def _start_and_submit(api_client, task_id: int, token: str) -> int:
    started = api_client.post(
        f"/tasks/{task_id}/participations/user", json={}, headers=_auth(token)
    )
    assert started.status_code == 200, started.text
    participant = started.json()["data"]["participant"]["id"]
    return _submit(api_client, task_id, participant, token), participant


def _submit(api_client, task_id: int, participant: int, token: str) -> int:
    resp = api_client.post(
        f"/tasks/{task_id}/participants/{participant}/submissions",
        json=[{"text": "我的作业"}],
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["submission"]["id"]


def _seed(
    db_session, _portal, task_id: int, rows: list[dict], *, unjudged: tuple = ()
) -> None:
    """写几行评测结果；``created_at`` 往后挪，好压过提交时自动排上的那一行。
    ``unjudged`` 那几版提交连自动排上的那一行也删掉：当它没记下代码版本、没排评测。"""

    async def seed() -> None:
        for submission_id in unjudged:
            await db_session.execute(
                delete(TaskJudgeRun).where(TaskJudgeRun.submission_id == submission_id)
            )
        suite_id = await db_session.scalar(
            select(TaskTestSuite.id).where(TaskTestSuite.task_id == task_id)
        )
        later = datetime.now(UTC) + timedelta(minutes=5)
        for i, row in enumerate(rows):
            db_session.add(
                TaskJudgeRun(
                    task_id=task_id,
                    suite_id=suite_id,
                    suite_revision=1,
                    code_sha="deadbeef",
                    requested_by=1,
                    created_at=later + timedelta(seconds=i),
                    **row,
                )
            )
        await db_session.flush()

    _portal.call(seed)


def _done(submission_id: int, passed: int, first: str, second: str, **more) -> dict:
    return {
        "submission_id": submission_id,
        "purpose": "SUBMISSION",
        "state": "done",
        "passed_points": passed,
        "total_points": 100,
        "results": _cases(first, second),
        **more,
    }


def _class_of_five(api_client, user_client, db_session, _portal):
    """五个人交了作业：三份评完（100、60、0 分），一份没评测，一份先评了 100、又交了
    一版判分失败的（只算最新那一版）。另有一次自测花了 1 额度。"""
    board = _new_board(user_client, api_client)
    task_id = _create_task(api_client, board, name="两数之和", testSuite=SUITE)
    submissions = []
    for _ in range(5):
        _, token = _member_of(user_client, api_client, board)
        submission, participant = _start_and_submit(api_client, task_id, token)
        submissions.append((submission, participant, token))
    s1, s2, s3, s4, s5 = (s for s, _p, _t in submissions)
    _p5, t5 = submissions[4][1], submissions[4][2]
    s5_new = _submit(api_client, task_id, _p5, t5)
    _seed(
        db_session,
        _portal,
        task_id,
        [
            _done(s1, 100, "ok", "ok"),
            _done(s2, 60, "ok", "timeout", credits=2.0, machine_ms=61_000),
            _done(s3, 0, "timeout", "timeout"),
            _done(s5, 100, "ok", "ok"),
            {
                "submission_id": s5_new,
                "purpose": "SUBMISSION",
                "state": "done",
                "error": {"key": "judgeScoreUnreadable", "params": {}},
            },
            {
                "purpose": "SELF_TEST",
                "trial_spec": {"participantId": submissions[0][1], "stage": 0},
                "state": "done",
                "credits": 1.0,
                "machine_ms": 5_000,
            },
        ],
        unjudged=(s4,),
    )
    return board, task_id, submissions


def test_the_setter_sees_each_case_s_pass_rate_and_the_score_spread(
    api_client: TestClient, user_client: UserCreator, db_session, _portal
):
    board, task_id, submissions = _class_of_five(
        api_client, user_client, db_session, _portal
    )

    resp = api_client.get(
        f"/tasks/{task_id}/judge/stats", headers=_auth(board["creator_token"])
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["stages"] == [None]
    stats = data["stats"]
    assert stats["kind"] == "IO"
    assert stats["submissions"] == 5
    # 只数最新那一版：第五个人先前那次 100 分不算，算的是后来判分失败的那次。
    assert (stats["judged"], stats["scoreFailed"], stats["failed"]) == (3, 1, 0)
    assert (stats["inFlight"], stats["notJudged"], stats["outdated"]) == (0, 1, 0)
    assert stats["averageRate"] == 0.5333
    buckets = [0] * 10
    buckets[0], buckets[6], buckets[9] = 1, 1, 1
    assert stats["buckets"] == buckets
    # 最差的用例在前，带着没过的那几份最常见的状态。
    assert stats["cases"] == [
        {
            "name": "隐藏的边界",
            "visibility": "HIDDEN",
            "judged": 3,
            "passed": 1,
            "rate": 0.3333,
            "topFailure": {"status": "timeout", "count": 2},
        },
        {
            "name": "公开的样例",
            "visibility": "SAMPLE",
            "judged": 3,
            "passed": 2,
            "rate": 0.6667,
            "topFailure": {"status": "timeout", "count": 1},
        },
    ]
    # 额度：提交的评测和自测都挂在这道题上。
    assert data["credits"] == {
        "total": 3.0,
        "submissions": 2.0,
        "selfTests": 1.0,
        "machineMs": 66_000,
        "chargedRuns": 2,
    }

    # 学生不是能批改的人：读不到。
    denied = api_client.get(
        f"/tasks/{task_id}/judge/stats", headers=_auth(submissions[0][2])
    )
    assert denied.status_code == 403, denied.text


def test_a_task_without_test_points_has_no_stats(
    api_client: TestClient, user_client: UserCreator
):
    board = _new_board(user_client, api_client)
    task_id = _create_task(api_client, board, name="没有测试点")
    resp = api_client.get(
        f"/tasks/{task_id}/judge/stats", headers=_auth(board["creator_token"])
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["stages"] == [] and data["stats"] is None and data["stage"] is None
    assert data["credits"]["total"] == 0


def test_task_difficulty_carries_the_class_test_pass_rate(
    api_client: TestClient, user_client: UserCreator, db_session, _portal
):
    board, task_id, _ = _class_of_five(api_client, user_client, db_session, _portal)
    on = api_client.patch(
        f"/spaces/{board['space_id']}/learning-analysis",
        json={"enabled": True},
        headers=_auth(board["creator_token"]),
    )
    assert on.status_code == 200, on.text
    # 一道也挂测试点、却只评了两份的题：不足 3 份，不给通过率。
    few = _create_task(api_client, board, name="人少的题", testSuite=SUITE)
    few_runs = []
    for _ in range(3):
        _, token = _member_of(user_client, api_client, board)
        submission, _p = _start_and_submit(api_client, few, token)
        few_runs.append(submission)
    _seed(
        db_session,
        _portal,
        few,
        [_done(few_runs[0], 100, "ok", "ok"), _done(few_runs[1], 60, "ok", "wrong")],
    )

    resp = api_client.get(
        f"/spaces/{board['space_id']}/learning-analysis/class/tasks",
        headers=_auth(board["creator_token"]),
    )
    assert resp.status_code == 200, resp.text
    rows = {row["taskId"]: row for row in resp.json()["data"]["tasks"]}
    assert rows[task_id]["analysed"] is True
    assert rows[task_id]["testPassRate"] == 0.5333
    assert rows[task_id]["testJudged"] == 3
    assert rows[few]["testPassRate"] is None
    assert rows[few]["testJudged"] == 2
    # 只有全班的数：这一行里没有任何人的名字或 id。
    assert set(rows[task_id]) >= {"testPassRate", "testJudged"}
    assert "participants" not in rows[task_id]
