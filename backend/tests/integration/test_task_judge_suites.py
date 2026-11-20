"""测试脚本、命令判分、学生自测、重新评测（方案 §测试脚本、§命令判分、§自测样例、
§改题后重新评测）。

评测主机还是 ``test_judge_dispatch`` 那个假的 hub：它不跑任何东西，按每条测试定好的样子
回一份评测脚本的回复，同时把收到的任务记下来 —— 所以这里看得到两头：派出去的任务里
带了什么（出题人的文件放在哪、自测只带样例），以及回复落库以后各人读到什么（学生看不到
隐藏测试的失败信息、判分失败不按 0 分算）。评测脚本自己怎么跑见
``tests/unit/test_judge_runner_suites.py``。
"""

# 夹具从 test_judge_dispatch 导入后又作参数用，ruff 把它读成重定义（F811）。
# ruff: noqa: F811

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import update

from app.domain.task import auto_score
from app.domain.task.models import TaskMembership
from tests.integration.conftest import UserCreator
from tests.integration.test_judge_dispatch import (
    SUITE as IO_SUITE,
)
from tests.integration.test_judge_dispatch import (
    _judge_of,
    _push,
    _ready,
    _runs,
    _submit,
    credits,  # noqa: F401 — 夹具
    hub,  # noqa: F401 — 夹具：假的评测主机
    judge,  # noqa: F401 — 夹具：走一拍评测
)
from tests.integration.test_task_files import (
    _auth,
    _new_board,
    upload_root,  # noqa: F401 — 建题要的那个夹具
)

SETTER_TEST = "from main import add\n\ndef test_small():\n    assert add(1, 2) == 3\n"
SCRIPT_SUITE = [
    {
        "kind": "SCRIPT",
        "runtime": "python3.12",
        "codeDir": "src",
        "runCmd": "python3 -m pytest --junitxml=$JUDGE_REPORT tests",
        "testFiles": [{"path": "test_add.py", "content": SETTER_TEST}],
        "cases": [
            {
                "testId": "tests.test_add::test_small",
                "points": 40,
                "visibility": "SAMPLE",
            },
            {"testId": "test_secret", "points": 60, "visibility": "HIDDEN"},
        ],
    }
]
COMMAND_SUITE = [
    {
        "kind": "COMMAND",
        "runtime": "python3.12",
        "commands": ["python3 grade.py"],
        "scoreSource": "JSON",
        "studentLogLines": 2,
        "testFiles": [{"path": "grade.py", "content": "print('grading')\n"}],
        "protectedFiles": [{"path": "src/api.py", "content": "API = 1\n"}],
    }
]


def _reply(**body) -> dict:
    return {
        "stdout": json.dumps({"ok": True, "machineMs": 1_000, **body}),
        "stderr": "",
        "exit": 0,
    }


def _task(api_client: TestClient, task_id: int, token: str) -> dict:
    resp = api_client.get(f"/tasks/{task_id}", headers=_auth(token))
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["task"]


# ---------------------------------------------------------------- 测试脚本


def test_a_test_script_s_files_reach_the_teacher_but_never_a_student(
    api_client: TestClient, user_client: UserCreator, hub, credits
):
    board, task_id, _, _, token, _ = _ready(
        api_client, user_client, credits, suite=SCRIPT_SUITE
    )
    teacher = _task(api_client, task_id, board["creator_token"])["testSuite"][0]
    assert teacher["testFiles"] == [
        {"path": "test_add.py", "bytes": len(SETTER_TEST), "content": SETTER_TEST}
    ]
    assert teacher["totalTimeLimitS"] == 120

    student = _task(api_client, task_id, token)["testSuite"][0]
    assert "testFiles" not in student
    assert student["testFileCount"] == 1
    assert [c.get("testId") for c in student["cases"]] == [
        "tests.test_add::test_small",
        "test_secret",
    ]
    assert SETTER_TEST not in json.dumps(student)

    # Sent back as read, the files are no change: the revision stays.
    same = api_client.patch(
        f"/tasks/{task_id}",
        json={"testSuite": [{**SCRIPT_SUITE[0], "testFiles": teacher["testFiles"]}]},
        headers=_auth(board["creator_token"]),
    )
    assert same.status_code == 200, same.text
    assert (
        _task(api_client, task_id, board["creator_token"])["testSuite"][0]["revision"]
        == teacher["revision"]
    )
    changed = api_client.patch(
        f"/tasks/{task_id}",
        json={
            "testSuite": [
                {
                    **SCRIPT_SUITE[0],
                    "testFiles": [{"path": "test_add.py", "content": "x"}],
                }
            ]
        },
        headers=_auth(board["creator_token"]),
    )
    assert changed.status_code == 200, changed.text
    assert (
        _task(api_client, task_id, board["creator_token"])["testSuite"][0]["revision"]
        == teacher["revision"] + 1
    )


def test_a_test_script_is_judged_test_by_test(
    api_client: TestClient, user_client: UserCreator, hub, judge, credits
):
    sent: list[dict] = []

    async def answer(job: dict) -> dict:
        sent.append(job)
        return _reply(
            tests=[
                {
                    "id": "tests.test_add::test_small",
                    "name": "test_small",
                    "status": "wrong",
                    "ms": 4,
                    "message": "assert 0 == 3",
                },
                {
                    "id": "tests.test_more::test_secret",
                    "name": "test_secret",
                    "status": "ok",
                    "ms": 2,
                    "message": "",
                },
                {
                    "id": "tests.test_more::test_extra",
                    "name": "test_extra",
                    "status": "wrong",
                    "message": "hidden detail",
                },
            ],
            log="1 failed, 2 passed",
        )

    hub.answer = answer
    board, task_id, participant, _, token, submission = _ready(
        api_client, user_client, credits, suite=SCRIPT_SUITE
    )
    judge()

    (job,) = sent
    assert job["kind"] == "SCRIPT" and job["totalTimeLimitS"] == 120
    # The setter's tests go under tests/ next to the code (codeDir = src).
    assert [
        (f["path"], base64.b64decode(f["data"]).decode()) for f in job["files"]
    ] == [("src/tests/test_add.py", SETTER_TEST)]
    assert job["cases"] == []

    mine = _judge_of(api_client, task_id, participant, submission, token)
    assert mine["state"] == "done" and mine["scoreFailed"] is False
    assert (mine["passedPoints"], mine["totalPoints"]) == (60, 100)
    by_id = {c["testId"]: c for c in mine["cases"]}
    # A sample test's failure is the student's to read; a hidden one's is not.
    assert by_id["tests.test_add::test_small"]["message"] == "assert 0 == 3"
    assert "message" not in by_id["tests.test_more::test_extra"]
    assert by_id["tests.test_more::test_extra"]["unscored"] is True
    assert "compileOutput" not in mine and "studentOutput" not in mine

    graders = _judge_of(
        api_client, task_id, participant, submission, board["creator_token"]
    )
    by_id = {c["testId"]: c for c in graders["cases"]}
    assert by_id["tests.test_more::test_extra"]["message"] == "hidden detail"
    assert graders["compileOutput"] == "1 failed, 2 passed"


# ---------------------------------------------------------------- 命令判分


def test_a_grading_command_that_gives_no_score_is_not_a_zero(
    api_client: TestClient,
    user_client: UserCreator,
    db_session,
    _portal,
    hub,
    judge,
    credits,
):
    sent: list[dict] = []

    async def no_score(job: dict) -> dict:
        sent.append(job)
        return _reply(scoreError="score_missing", log="grading\nline two\nline three")

    hub.answer = no_score
    board, task_id, participant, project_id, token, submission = _ready(
        api_client, user_client, credits, suite=COMMAND_SUITE
    )
    judge()

    (job,) = sent
    assert job["commands"] == ["python3 grade.py"] and job["scoreSource"] == "JSON"
    # Protected and grader files go back at the paths the setter named.
    assert sorted(f["path"] for f in job["files"]) == ["grade.py", "src/api.py"]

    mine = _judge_of(api_client, task_id, participant, submission, token)
    assert mine["state"] == "done" and mine["scoreFailed"] is True
    assert mine["error"]["key"] == "judgeScoreMissing"
    assert mine["studentOutput"] == "grading\nline two"
    assert "compileOutput" not in mine
    graders = _judge_of(
        api_client, task_id, participant, submission, board["creator_token"]
    )
    assert graders["compileOutput"] == "grading\nline two\nline three"

    # The tests line waits for the teacher instead of reading 0.
    line = SimpleNamespace(index=0, points=40)
    entry, settled = _portal.call(
        auto_score._tests_entry, db_session, submission, line, None
    )
    assert entry["pending"] is True and settled is False

    # A failed scoring is no answer to reuse: the same commit is judged again.
    hub.answer = lambda job: _awaitable(
        _reply(score={"score": 7.5, "max": 10, "items": []}, log="")
    )
    again = _submit(api_client, task_id, participant, token)
    assert (
        _judge_of(api_client, task_id, participant, again, token)["state"] == "queued"
    )
    judge()
    scored = _judge_of(api_client, task_id, participant, again, token)
    assert (scored["passedPoints"], scored["totalPoints"]) == (750, 1000)
    assert scored["cases"][0]["score"] == 7.5


async def _awaitable(value):
    return value


# ---------------------------------------------------------------- 自测


def _self_test(api_client, task_id, participant, token, method="post"):
    call = getattr(api_client, method)
    path = f"/tasks/{task_id}/participants/{participant}/self-tests"
    return call(path if method == "post" else path + "/latest", headers=_auth(token))


def test_a_student_self_tests_the_samples_a_few_times_a_day(
    api_client: TestClient, user_client: UserCreator, hub, judge, credits
):
    sent: list[dict] = []
    passes = hub.answer

    async def answer(job: dict) -> dict:
        sent.append(job)
        return await passes(job)

    hub.answer = answer
    suite = [{**IO_SUITE[0], "selfTestDaily": 1}]
    _, task_id, participant, _, token, _ = _ready(
        api_client, user_client, credits, suite=suite
    )
    judge()  # the submission's own judging
    sent.clear()

    first = _self_test(api_client, task_id, participant, token)
    assert first.status_code == 200, first.text
    body = first.json()["data"]
    assert body["selfTest"]["state"] == "queued"
    assert body["selfTest"]["purpose"] == "SELF_TEST"
    assert (body["dailyLimit"], body["usedToday"]) == (1, 1)
    judge()

    (job,) = sent
    # Only the sample case goes to the judge host: the hidden one never does.
    assert [c["expected"] for c in job["cases"]] == ["3\n"]
    latest = _self_test(api_client, task_id, participant, token, "get")
    assert latest.status_code == 200, latest.text
    done = latest.json()["data"]["selfTest"]
    assert done["state"] == "done" and [c["status"] for c in done["cases"]] == ["ok"]

    again = _self_test(api_client, task_id, participant, token)
    assert again.status_code == 400, again.text
    assert again.json()["error"]["i18n"]["key"] == "judgeSelfTestLimit"


def test_self_testing_is_refused_when_off_not_io_or_past_the_deadline(
    api_client: TestClient, user_client: UserCreator, db_session, _portal, hub, credits
):
    _, task_id, participant, _, token, _ = _ready(api_client, user_client, credits)
    off = _self_test(api_client, task_id, participant, token)
    assert off.json()["error"]["i18n"]["key"] == "judgeSelfTestOff"

    _, task_id, participant, _, token, _ = _ready(
        api_client,
        user_client,
        credits,
        suite=[{**SCRIPT_SUITE[0], "selfTestDaily": 3}],
    )
    script = _self_test(api_client, task_id, participant, token)
    assert script.json()["error"]["i18n"]["key"] == "judgeSelfTestIoOnly"

    _, task_id, participant, _, token, _ = _ready(
        api_client, user_client, credits, suite=[{**IO_SUITE[0], "selfTestDaily": 3}]
    )

    async def past_deadline():
        await db_session.execute(
            update(TaskMembership)
            .where(TaskMembership.id == participant)
            .values(deadline=datetime.now(UTC) - timedelta(hours=1))
        )

    _portal.call(past_deadline)
    late = _self_test(api_client, task_id, participant, token)
    assert late.status_code == 400, late.text
    assert late.json()["error"]["i18n"]["key"] == "submissionPastDeadline"


# ---------------------------------------------------------------- 重新评测


def test_a_teacher_re_judges_every_latest_submission_after_changing_the_tests(
    api_client: TestClient,
    user_client: UserCreator,
    db_session,
    _portal,
    hub,
    judge,
    credits,
):
    board, task_id, participant, project_id, token, _ = _ready(
        api_client, user_client, credits
    )
    # A second version: only the latest of each participant is re-judged.
    _push(project_id, "print(sum(map(int, input().split())))\n")
    latest = _submit(api_client, task_id, participant, token)
    judge()
    judge()

    denied = api_client.post(f"/tasks/{task_id}/judge/rejudge", headers=_auth(token))
    assert denied.status_code == 403, denied.text

    nothing_new = api_client.post(
        f"/tasks/{task_id}/judge/rejudge", headers=_auth(board["creator_token"])
    )
    assert nothing_new.status_code == 200, nothing_new.text
    assert nothing_new.json()["data"] == {
        "queued": 0,
        "reused": 0,
        "unchanged": 1,
        "skipped": 0,
    }

    changed = [{**IO_SUITE[0], "cases": [{**IO_SUITE[0]["cases"][0], "points": 100}]}]
    edited = api_client.patch(
        f"/tasks/{task_id}",
        json={"testSuite": changed},
        headers=_auth(board["creator_token"]),
    )
    assert edited.status_code == 200, edited.text
    queued = api_client.post(
        f"/tasks/{task_id}/judge/rejudge", headers=_auth(board["creator_token"])
    )
    assert queued.json()["data"]["queued"] == 1
    judge()

    result = _judge_of(api_client, task_id, participant, latest, token)
    assert result["state"] == "done" and result["suiteRevision"] == 2
    assert (result["passedPoints"], result["totalPoints"]) == (100, 100)
    runs = _runs(db_session, _portal, task_id)
    assert [r.submission_id for r in runs].count(latest) == 2


# ---------------------------------------------------------------- 试跑


def test_a_setter_tries_a_test_script_with_reference_code(
    api_client: TestClient, user_client: UserCreator, hub, judge, credits
):
    sent: list[dict] = []

    async def answer(job: dict) -> dict:
        sent.append(job)
        return _reply(
            tests=[
                {
                    "id": "tests.test_add::test_small",
                    "name": "test_small",
                    "status": "ok",
                }
            ],
            log="1 passed",
        )

    hub.answer = answer
    board = _new_board(user_client, api_client)
    credits(100)
    tried = api_client.post(
        f"/spaces/{board['space_id']}/test-trials",
        json={
            "testSuite": SCRIPT_SUITE[0],
            "files": [
                {"path": "src/main.py", "content": "def add(a, b): return a + b\n"}
            ],
        },
        headers=_auth(board["creator_token"]),
    )
    assert tried.status_code == 200, tried.text
    run_id = tried.json()["data"]["trial"]["runId"]
    judge()

    (job,) = sent
    assert [f["path"] for f in job["files"]] == ["src/tests/test_add.py"]
    read = api_client.get(
        f"/tasks/test-trials/{run_id}", headers=_auth(board["creator_token"])
    )
    trial = read.json()["data"]["trial"]
    assert trial["state"] == "done"
    # The setter configured 40 + 60 points; the hidden test never ran.
    assert (trial["passedPoints"], trial["totalPoints"]) == (40, 100)
    assert {c["testId"]: c["status"] for c in trial["cases"]} == {
        "tests.test_add::test_small": "ok",
        "test_secret": "missing",
    }
