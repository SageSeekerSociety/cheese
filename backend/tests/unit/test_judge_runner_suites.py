"""测试脚本与命令判分在评测主机上怎么跑、怎么读结果（方案 §测试脚本、§命令判分）。

docker 还是 ``tests/support/fake_docker.py`` 那个替身：命令在本机用 ``sh -c`` 跑，挂载
的目录和 ``$JUDGE_REPORT`` 都换成本机上的同一处。测试脚本那几条用本机这份真的 pytest
（``sys.executable -m pytest``）写真的 JUnit XML 报告，所以「学生的 conftest.py 不被
加载」「出题人的同名文件盖过学生的」都是 pytest 自己跑出来的结果，不是假设。
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys

import pytest

from app.domain.judge import reports, runner
from app.domain.judge.script import RUNNER_SRC
from tests.unit.test_judge_runner import _code, fake_docker  # noqa: F401 — 夹具

PYTEST = f"{sys.executable} -m pytest -q -p no:cacheprovider --junitxml=$JUDGE_REPORT"


def _file(path: str, text: str) -> dict:
    return {"path": path, "data": base64.b64encode(text.encode()).decode()}


def _script_job(files: dict[str, str], tests: dict[str, str], **over) -> dict:
    job = {
        "kind": "SCRIPT",
        "image": "judge:test",
        "code": _code(files),
        "codeDir": "",
        "runCmd": f"{PYTEST} tests",
        "memoryMb": 512,
        "totalTimeLimitS": 60,
        "files": [_file(f"tests/{name}", text) for name, text in tests.items()],
    }
    job.update(over)
    return job


def _command_job(code: dict[str, str], commands: list[str], **over) -> dict:
    job = {
        "kind": "COMMAND",
        "image": "judge:test",
        "code": _code(code),
        "codeDir": "",
        "commands": commands,
        "scoreSource": "LINE",
        "memoryMb": 512,
        "totalTimeLimitS": 60,
        "files": [],
    }
    job.update(over)
    return job


SOLUTION = "def add(a, b):\n    return a + b\n"
SETTER_TESTS = (
    "from solution import add\n\n"
    "def test_small():\n    assert add(1, 2) == 3\n\n"
    "def test_negative():\n    assert add(-1, -2) == -3\n\n"
    "def test_wrong_on_purpose():\n    assert add(2, 2) == 5, 'two and two'\n"
)


def _by_name(reply: dict) -> dict[str, dict]:
    assert reply["ok"], reply
    assert "scoreError" not in reply, reply
    return {test["name"]: test for test in reply["tests"]}


# ---------------------------------------------------------------- 测试脚本


def test_a_test_script_reports_every_test_by_name_and_outcome(fake_docker):
    reply = runner.judge(
        _script_job({"solution.py": SOLUTION}, {"test_add.py": SETTER_TESTS})
    )
    tests = _by_name(reply)
    assert {name: t["status"] for name, t in tests.items()} == {
        "test_small": "ok",
        "test_negative": "ok",
        "test_wrong_on_purpose": "wrong",
    }
    assert tests["test_small"]["id"] == "tests.test_add::test_small"
    assert "two and two" in tests["test_wrong_on_purpose"]["message"]


def test_the_setters_test_file_wins_over_the_students_of_the_same_name(fake_docker):
    rigged = "def test_small():\n    pass\n"
    reply = runner.judge(
        _script_job(
            {
                "solution.py": "def add(a, b):\n    return 0\n",
                "tests/test_add.py": rigged,
            },
            {"test_add.py": SETTER_TESTS},
        )
    )
    tests = _by_name(reply)
    assert tests["test_small"]["status"] == "wrong"
    assert set(tests) == {"test_small", "test_negative", "test_wrong_on_purpose"}


def test_a_link_the_student_left_in_the_way_does_not_redirect_the_setters_file(
    fake_docker, tmp_path
):
    outside = tmp_path / "outside.py"
    outside.write_text("untouched\n")
    work = tmp_path / "w"
    work.mkdir()
    (work / "tests").mkdir()
    os.symlink(outside, work / "tests" / "test_add.py")
    runner._place(str(work), "tests/test_add.py", b"setter\n")
    assert outside.read_text() == "untouched\n"
    assert not os.path.islink(work / "tests" / "test_add.py")
    assert (work / "tests" / "test_add.py").read_text() == "setter\n"


def test_the_students_conftest_is_not_loaded_but_the_setters_is(fake_docker):
    # A conftest.py hook can turn every failure into a pass; the student's
    # own never gets to run.
    cheat = (
        "import pytest\n\n"
        "@pytest.hookimpl(hookwrapper=True)\n"
        "def pytest_runtest_makereport(item, call):\n"
        "    outcome = yield\n"
        "    report = outcome.get_result()\n"
        "    report.outcome = 'passed'\n"
    )
    setters = "import pytest\n\n@pytest.fixture\ndef two():\n    return 2\n"
    uses_fixture = "from solution import add\n\ndef test_fixture(two):\n    assert add(two, two) == 4\n"
    reply = runner.judge(
        _script_job(
            {
                "solution.py": SOLUTION,
                "conftest.py": cheat,
                "tests/sub/conftest.py": cheat,
            },
            {
                "test_add.py": SETTER_TESTS,
                "conftest.py": setters,
                "sub/test_more.py": uses_fixture,
            },
        )
    )
    tests = _by_name(reply)
    assert tests["test_wrong_on_purpose"]["status"] == "wrong"
    assert tests["test_fixture"]["status"] == "ok"


def test_no_report_is_a_scoring_failure_not_a_zero(fake_docker):
    reply = runner.judge(
        _script_job(
            {"solution.py": SOLUTION}, {"test_add.py": SETTER_TESTS}, runCmd="true"
        )
    )
    assert reply["ok"] and reply["scoreError"] == "report_missing"
    assert "tests" not in reply


def test_a_malformed_report_is_a_scoring_failure_with_the_output(fake_docker):
    reply = runner.judge(
        _script_job(
            {"solution.py": SOLUTION},
            {"test_add.py": SETTER_TESTS},
            runCmd="echo writing; echo '<testsuites><testcase' > $JUDGE_REPORT",
        )
    )
    assert reply["scoreError"] == "report_malformed"
    assert "writing" in reply["log"]


def test_a_report_with_entities_is_refused(fake_docker):
    bomb = (
        '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]>'
        '<testsuite><testcase name="t">&a;</testcase></testsuite>'
    )
    with pytest.raises(reports.ReportError) as raised:
        reports.parse_junit(bomb.encode())
    assert raised.value.code == "report_malformed"


def test_a_report_too_large_is_not_read(fake_docker, monkeypatch):
    monkeypatch.setattr(runner, "REPORT_MAX_BYTES", 1024)
    reply = runner.judge(
        _script_job(
            {"solution.py": SOLUTION},
            {"test_add.py": SETTER_TESTS},
            runCmd="head -c 5000 /dev/zero > $JUDGE_REPORT",
        )
    )
    assert reply["scoreError"] == "report_too_large"


def test_a_lone_testsuite_root_with_skips_and_errors_reads(fake_docker):
    xml = (
        b'<testsuite name="node">'
        b'<testcase name="adds" classname="math" time="0.012"/>'
        b'<testcase name="later" classname="math"><skipped/></testcase>'
        b'<testcase name="boom" classname="math"><error message="TypeError"/></testcase>'
        b"</testsuite>"
    )
    tests, truncated = reports.parse_junit(xml)
    assert not truncated
    assert [(t["id"], t["status"], t["ms"]) for t in tests] == [
        ("math::adds", "ok", 12),
        ("math::later", "skipped", None),
        ("math::boom", "error", None),
    ]
    assert tests[2]["message"] == "TypeError"


def test_a_run_past_its_total_time_limit_is_stopped(fake_docker, monkeypatch):
    monkeypatch.setattr(runner, "STARTUP_S", 0.2)
    reply = runner.judge(
        _script_job(
            {"solution.py": SOLUTION},
            {"test_add.py": SETTER_TESTS},
            runCmd="sleep 30",
            totalTimeLimitS=1,
        )
    )
    assert reply["stopped"] == "timeout"
    assert reply["scoreError"] == "report_missing"
    assert reply["machineMs"] < 10_000


# ---------------------------------------------------------------- 命令判分


def test_the_last_score_line_is_the_score(fake_docker):
    reply = runner.judge(
        _command_job(
            {"main.sh": "echo hi\n"},
            ["sh main.sh", "echo 'SCORE: 1/9'", "echo 'SCORE: 7/10'"],
        )
    )
    assert reply["score"] == {"score": 7, "max": 10, "items": []}
    assert reply["log"].splitlines()[0] == "hi"


def test_commands_stop_at_the_first_that_fails(fake_docker):
    reply = runner.judge(
        _command_job({}, ["echo first", "exit 4", "echo 'SCORE: 10/10'"])
    )
    assert reply["scoreError"] == "score_missing"
    assert "[command 2 exited 4]" in reply["log"]
    assert "SCORE" not in reply["log"]


def test_score_json_is_read_with_its_items(fake_docker):
    write = (
        'echo \'{"score": 7.5, "max": 10, "items": ['
        '{"name": "a", "score": 5, "max": 5}, '
        '{"name": "b", "score": 2.5, "max": 5, "visible": false}]}\' > score.json'
    )
    reply = runner.judge(_command_job({}, [write], scoreSource="JSON"))
    assert reply["score"]["score"] == 7.5
    assert reply["score"]["items"][1] == {
        "name": "b",
        "score": 2.5,
        "max": 5,
        "visible": False,
    }


def test_a_score_json_the_student_committed_is_not_a_score(fake_docker):
    committed = json.dumps({"score": 10, "max": 10})
    reply = runner.judge(
        _command_job({"score.json": committed}, ["true"], scoreSource="JSON")
    )
    assert reply["scoreError"] == "score_missing"


def test_a_broken_score_json_is_a_scoring_failure(fake_docker):
    reply = runner.judge(
        _command_job(
            {}, ['echo \'{"score": "lots"}\' > score.json'], scoreSource="JSON"
        )
    )
    assert reply["scoreError"] == "score_malformed"


def test_protected_files_are_put_back_before_the_commands_run(fake_docker):
    reply = runner.judge(
        _command_job(
            {"grader.sh": "echo 'SCORE: 10/10'\n", "lib/data.txt": "student\n"},
            ["cat lib/data.txt", "sh grader.sh"],
            files=[
                _file("grader.sh", "echo 'SCORE: 3/10'\n"),
                _file("lib/data.txt", "setter\n"),
            ],
        )
    )
    assert reply["score"]["score"] == 3
    assert reply["log"].splitlines()[0] == "setter"


def test_a_command_run_past_its_total_time_limit_is_stopped(fake_docker, monkeypatch):
    monkeypatch.setattr(runner, "STARTUP_S", 0.2)
    reply = runner.judge(
        _command_job({}, ["sleep 30", "echo 'SCORE: 1/1'"], totalTimeLimitS=1)
    )
    assert reply["stopped"] == "timeout"
    assert reply["scoreError"] == "score_missing"


def test_the_teachers_log_keeps_the_first_lines_only(fake_docker, monkeypatch):
    monkeypatch.setattr(runner, "LOG_LINES", 5)
    reply = runner.judge(_command_job({}, ["seq 1 100", "echo 'SCORE: 1/2'"]))
    assert reply["log"].splitlines() == ["1", "2", "3", "4", "5"]
    assert reply["score"]["score"] == 1


def test_the_script_sent_to_the_host_judges_commands_on_its_own(fake_docker):
    job = _command_job({}, ["echo 'SCORE: 2/4'"])
    done = subprocess.run(
        [sys.executable, "-I", "-c", RUNNER_SRC],
        input=json.dumps(job),
        capture_output=True,
        text=True,
        timeout=60,
        env={**os.environ},
        check=True,
    )
    assert json.loads(done.stdout)["score"]["max"] == 4
