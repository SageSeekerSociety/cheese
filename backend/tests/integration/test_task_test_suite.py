"""一道题的测试点（方案 §出题人：配测试点、§数据结构）。

这一条走完整的 HTTP 路：出题表单把 ``testSuite`` 一起发上来就存下了，读的时候按看的
人裁；改题整份替换它（改到它是新的一版）。测试脚本和命令判分另见
``test_task_judge_suites``。

三条断言各钉一件事：

1. 出题人给的一套测试点，读回来就是它（含每个用例的输入与期望输出）；
2. 学生读到隐藏用例的名称、分值和可见性，但拿不到它的输入与期望输出 —— 那两格在他
   的响应里根本不存在；
3. 测试脚本与命令判分这一步只能离线跑：要装依赖的被拒（``testSuiteInstallDepsLater``），
   三种以外的形态拒（``testSuiteKindUnknown``）。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.integration.conftest import UserCreator
from tests.integration.test_task_files import (
    _auth,
    _create_task,
    _member_of,
    _new_board,
    _post_task,
    upload_root,  # noqa: F401 — 建题要的那个夹具
)

SUITE = [
    {
        "kind": "IO",
        "runtime": "python3.12",
        "codeDir": "src",
        "compileCmd": "",
        "runCmd": "python3 main.py",
        "timeLimitMs": 1000,
        "memoryMb": 256,
        "compare": "TRIM_TRAILING",
        "tolerance": 0.0,
        "cases": [
            {
                "name": "样例 1",
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


def _read(api_client: TestClient, task_id: int, token: str) -> dict:
    resp = api_client.get(f"/tasks/{task_id}", headers=_auth(token))
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["task"]


def test_the_publisher_s_test_suite_is_read_back_by_the_grader(
    api_client: TestClient, user_client: UserCreator
):
    board = _new_board(user_client, api_client)
    task_id = _create_task(api_client, board, name="带测试点的题", testSuite=SUITE)

    suite = _read(api_client, task_id, board["creator_token"])["testSuite"]
    assert len(suite) == 1
    assert (suite[0]["kind"], suite[0]["runtime"], suite[0]["runCmd"]) == (
        "IO",
        "python3.12",
        "python3 main.py",
    )
    assert [c["name"] for c in suite[0]["cases"]] == ["样例 1", "隐藏的边界"]
    # 出题人读到每个用例的输入与期望输出，隐藏的也一样。
    assert [c["input"] for c in suite[0]["cases"]] == ["1 2\n", "100 200\n"]
    assert [c["expected"] for c in suite[0]["cases"]] == ["3\n", "300\n"]


def test_a_student_reads_a_hidden_case_without_its_bytes(
    api_client: TestClient, user_client: UserCreator
):
    board = _new_board(user_client, api_client)
    task_id = _create_task(
        api_client, board, name="隐藏用例不进学生响应", testSuite=SUITE
    )
    _, student_token = _member_of(user_client, api_client, board)

    suite = _read(api_client, task_id, student_token)["testSuite"]
    assert len(suite) == 1
    by_name = {c["name"]: c for c in suite[0]["cases"]}
    # 样例用例本来公开：输入、期望都在。
    assert by_name["样例 1"]["input"] == "1 2\n"
    assert by_name["样例 1"]["expected"] == "3\n"
    # 隐藏用例：名称、分值、可见性在；输入与期望那两格不存在。
    hidden = by_name["隐藏的边界"]
    assert hidden["visibility"] == "HIDDEN"
    assert hidden["points"] == 40
    assert "input" not in hidden
    assert "expected" not in hidden


def test_installing_dependencies_is_refused_while_judging_is_offline(
    api_client: TestClient, user_client: UserCreator
):
    board = _new_board(user_client, api_client)
    for kind in ("SCRIPT", "COMMAND"):
        resp = _post_task(
            api_client,
            board,
            name=f"要装依赖的 {kind}",
            testSuite=[
                {
                    **SUITE[0],
                    "kind": kind,
                    "installDeps": True,
                    "commands": ["true"],
                    "testFiles": [{"path": "t.py", "content": ""}],
                }
            ],
        )
        assert resp.status_code == 400, resp.text
        assert resp.json()["error"]["i18n"]["key"] == "testSuiteInstallDepsLater"
    # 形态根本不是三种之一：另一句话。
    resp = _post_task(
        api_client,
        board,
        name="不认识的形态",
        testSuite=[{**SUITE[0], "kind": "MAGIC"}],
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["error"]["i18n"]["key"] == "testSuiteKindUnknown"


def test_editing_replaces_the_whole_suite(
    api_client: TestClient, user_client: UserCreator
):
    board = _new_board(user_client, api_client)
    task_id = _create_task(api_client, board, name="改测试点", testSuite=SUITE)

    resp = api_client.patch(
        f"/tasks/{task_id}",
        json={
            "testSuite": [
                {
                    "kind": "IO",
                    "runtime": "node22",
                    "runCmd": "node main.js",
                    "cases": [
                        {
                            "name": "唯一一个",
                            "points": 100,
                            "visibility": "SAMPLE",
                            "input": "a",
                            "expected": "b",
                        }
                    ],
                }
            ]
        },
        headers=_auth(board["creator_token"]),
    )
    assert resp.status_code == 200, resp.text
    suite = _read(api_client, task_id, board["creator_token"])["testSuite"]
    assert len(suite) == 1
    assert suite[0]["runtime"] == "node22"
    assert [c["name"] for c in suite[0]["cases"]] == ["唯一一个"]
    assert suite[0]["cases"][0]["expected"] == "b"


def test_a_suite_needs_a_run_command_and_a_case(
    api_client: TestClient, user_client: UserCreator
):
    board = _new_board(user_client, api_client)
    resp = _post_task(
        api_client,
        board,
        name="没有用例",
        testSuite=[{**SUITE[0], "cases": []}],
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["error"]["i18n"]["key"] == "testSuiteNoCases"

    resp = _post_task(
        api_client,
        board,
        name="没有运行命令",
        testSuite=[{**SUITE[0], "runCmd": ""}],
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["error"]["i18n"]["key"] == "testSuiteNoRunCommand"


def test_a_tests_rubric_line_needs_a_stage_with_test_cases(
    api_client: TestClient, user_client: UserCreator
):
    """评分要点里设为「测试点」的那一项，指着有测试点的阶段；没有就拒。"""
    board = _new_board(user_client, api_client)
    # 没有测试点，却把一项设为 TESTS。
    resp = _post_task(
        api_client,
        board,
        name="有 TESTS 项却没有测试点",
        rubric=[{"item": "正确性", "points": 100, "criteria": "", "kind": "tests"}],
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["error"]["i18n"]["key"] == "rubricTestsWithoutSuite"

    # 有测试点时收下，读回来那一项的 kind 是 tests。
    task_id = _create_task(
        api_client,
        board,
        name="有 TESTS 项也有测试点",
        testSuite=SUITE,
        rubric=[{"item": "正确性", "points": 100, "criteria": "", "kind": "tests"}],
    )
    rubric = _read(api_client, task_id, board["creator_token"])["rubric"]
    assert rubric[0]["kind"] == "tests"
