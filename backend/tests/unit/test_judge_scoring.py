"""评测主机的回复怎么变成结果行和分数（``app.domain.judge.scoring``），以及出题表单里
测试脚本、命令判分那几格怎么校验（``app.domain.task.test_suite.suite_lines``）。

都是纯函数：给一份回复、一份表单，看出来的分、行和拒绝的那句话。
"""

from __future__ import annotations

import base64

import pytest

from app.core.errors import BadRequestError
from app.domain.judge import scoring
from app.domain.task.test_suite import suite_lines

SETTINGS = {"totalTimeLimitS": 120, "studentLogLines": 0}


def _test(name: str, status: str, cls: str = "tests.test_add", **more) -> dict:
    return {
        "id": f"{cls}::{name}",
        "name": name,
        "classname": cls,
        "status": status,
        "ms": 3,
        **more,
    }


def _case(index: int, test_id: str, points: int, visibility: str = "SAMPLE") -> dict:
    return {
        "index": index,
        "id": 100 + index,
        "name": test_id,
        "points": points,
        "visibility": visibility,
        "testId": test_id,
    }


# ---------------------------------------------------------------- 测试脚本


def test_without_configured_points_every_test_weighs_the_same():
    reply = {
        "tests": [
            _test("a", "ok"),
            _test("b", "wrong", message="assert 1 == 2"),
            _test("c", "skipped"),
            _test("d", "ok"),
        ]
    }
    got = scoring.script([], reply, SETTINGS)
    assert (got.passed, got.total, got.error) == (2, 4, None)
    assert [r["status"] for r in got.rows] == ["ok", "wrong", "wrong", "ok"]
    assert got.rows[2]["skipped"] is True
    # Unconfigured tests show name and status; their messages are the teacher's.
    assert all(r["visibility"] == "HIDDEN" for r in got.rows)


def test_configured_points_count_only_the_configured_tests():
    cases = [
        _case(0, "tests.test_add::a", 30),
        _case(1, "b", 50, "HIDDEN"),  # just the name: unique in the report
        _case(2, "tests.test_add::gone", 20),
    ]
    reply = {"tests": [_test("a", "ok"), _test("b", "ok"), _test("extra", "ok")]}
    got = scoring.script(cases, reply, SETTINGS)
    assert (got.passed, got.total) == (80, 100)
    by_id = {r["testId"]: r for r in got.rows}
    assert by_id["tests.test_add::gone"]["status"] == "missing"
    extra = by_id["tests.test_add::extra"]
    assert extra["points"] == 0 and extra["unscored"] is True
    assert by_id["tests.test_add::b"]["visibility"] == "HIDDEN"


def test_a_bare_name_that_two_tests_share_matches_neither():
    cases = [_case(0, "same", 10)]
    reply = {"tests": [_test("same", "ok", cls="one"), _test("same", "ok", cls="two")]}
    got = scoring.script(cases, reply, SETTINGS)
    assert (got.passed, got.total) == (0, 10)
    assert got.rows[0]["status"] == "missing"


@pytest.mark.parametrize(
    ("reply", "key"),
    [
        ({"scoreError": "report_missing"}, "judgeReportMissing"),
        ({"scoreError": "report_malformed"}, "judgeReportMalformed"),
        ({"scoreError": "report_missing", "stopped": "timeout"}, "judgeScoreTimedOut"),
        ({"tests": []}, "judgeReportEmpty"),
    ],
)
def test_an_unreadable_report_is_a_scoring_failure_with_no_score(reply, key):
    got = scoring.script([_case(0, "a", 10)], {**reply, "log": "boom"}, SETTINGS)
    assert got.error is not None and got.error.key == key
    assert (got.passed, got.total, got.rows) == (None, None, [])
    assert got.log == "boom"


def test_students_read_only_the_lines_the_setter_allows():
    log = "\n".join(f"line {i}" for i in range(10))
    reply = {"scoreError": "report_missing", "log": log}
    none = scoring.script([], reply, SETTINGS)
    some = scoring.script([], reply, {**SETTINGS, "studentLogLines": 3})
    assert none.student_log is None
    assert some.student_log == "line 0\nline 1\nline 2"
    assert some.log == log


# ---------------------------------------------------------------- 命令判分


def test_whole_scores_are_kept_as_they_are():
    got = scoring.command({"score": {"score": 7, "max": 10, "items": []}}, SETTINGS)
    assert (got.passed, got.total, got.error) == (7, 10, None)
    assert got.rows[0]["score"] == 7 and got.rows[0]["maxScore"] == 10


def test_fractional_scores_keep_their_ratio():
    got = scoring.command({"score": {"score": 7.5, "max": 10, "items": []}}, SETTINGS)
    assert (got.passed, got.total) == (750, 1000)


@pytest.mark.parametrize(
    "score",
    [
        {"score": 11, "max": 10},
        {"score": -1, "max": 10},
        {"score": 0, "max": 0},
        {"score": True, "max": 10},
        # 学生代码写得了 score.json：大到 float() 溢出的整数、JSON 里的无穷大，
        # 都只是一个读不懂的分，不能让这一轮派发出异常。
        {"score": 1, "max": 10**400},
        {"score": float("inf"), "max": float("inf")},
        {"score": 5, "max": float("nan")},
    ],
)
def test_a_score_outside_zero_to_max_is_a_scoring_failure(score):
    got = scoring.command({"score": {**score, "items": []}, "log": "x"}, SETTINGS)
    assert got.error is not None and got.error.key == "judgeScoreOutOfRange"
    assert got.passed is None and got.total is None


def test_items_become_rows_and_invisible_ones_are_private():
    found = {
        "score": 6,
        "max": 10,
        "items": [
            {"name": "build", "score": 5, "max": 5, "visible": True},
            {"name": "secret", "score": 1, "max": 5, "visible": False},
        ],
    }
    got = scoring.command({"score": found}, SETTINGS)
    assert [(r["name"], r["status"], r.get("private")) for r in got.rows] == [
        ("build", "ok", False),
        ("secret", "wrong", True),
    ]


def test_no_score_after_a_timeout_says_it_timed_out():
    got = scoring.command(
        {"scoreError": "score_missing", "stopped": "timeout"},
        {**SETTINGS, "totalTimeLimitS": 300},
    )
    assert got.error is not None
    assert got.error.key == "judgeScoreTimedOut"
    assert got.error.params == {"seconds": 300}


# ---------------------------------------------------------------- 出题表单


def _script(**over) -> dict:
    entry = {
        "kind": "SCRIPT",
        "runtime": "python3.12",
        "runCmd": "pytest --junitxml=$JUDGE_REPORT tests",
        "testFiles": [{"path": "test_add.py", "content": "def test_a(): pass\n"}],
    }
    entry.update(over)
    return entry


def _command(**over) -> dict:
    entry = {
        "kind": "COMMAND",
        "runtime": "python3.12",
        "commands": ["python3 grade.py"],
        "testFiles": [{"path": "grade.py", "content": "print('SCORE: 1/1')\n"}],
    }
    entry.update(over)
    return entry


def _refused(entry: dict) -> str:
    with pytest.raises(BadRequestError) as raised:
        suite_lines([entry])
    return raised.value.args[0].key


def test_a_test_script_gets_its_defaults():
    (line,) = suite_lines([_script()])
    assert line.kind == "SCRIPT"
    assert line.total_time_limit_s == 120
    assert line.memory_mb == 512
    assert line.cases == ()
    assert [f.path for f in line.files] == ["test_add.py"]


def test_a_grading_command_gets_its_defaults_and_protected_paths():
    (line,) = suite_lines(
        [
            _command(
                protectedFiles=[
                    {"path": "src/api.py", "content": "x"},
                    {"path": "data.bin", "base64": base64.b64encode(b"\0\1").decode()},
                ]
            )
        ]
    )
    assert line.total_time_limit_s == 300
    assert line.score_source == "LINE"
    assert line.protected_paths == ["src/api.py", "data.bin"]
    assert [f.data for f in line.files if f.path == "data.bin"] == [b"\0\1"]


@pytest.mark.parametrize(
    ("entry", "key"),
    [
        (_script(installDeps=True), "testSuiteInstallDepsLater"),
        (_command(installDeps=True), "testSuiteInstallDepsLater"),
        (_script(testFiles=[]), "testSuiteNoTestFiles"),
        (_script(totalTimeLimitS=10), "testSuiteTotalTimeRange"),
        (_script(totalTimeLimitS=601), "testSuiteTotalTimeRange"),
        (_script(testFiles=[{"path": "../x.py"}]), "testSuiteFileBadPath"),
        (_script(testFiles=[{"path": "/etc/x"}]), "testSuiteFileBadPath"),
        (
            _script(testFiles=[{"path": "a.py"}, {"path": "a.py"}]),
            "testSuiteFileDuplicate",
        ),
        (_script(cases=[{"points": 1}]), "testSuiteTestIdMissing"),
        (
            _script(cases=[{"testId": "a", "points": 1}, {"testId": "a"}]),
            "testSuiteTestIdDuplicate",
        ),
        (_script(studentLogLines=500), "testSuiteLogLinesRange"),
        (_command(commands=[]), "testSuiteNoCommands"),
        (_command(commands=["true"] * 11), "testSuiteTooManyCommands"),
        (_command(scoreSource="XML"), "testSuiteScoreSourceUnknown"),
        (
            _command(protectedFiles=[{"path": "grade.py", "content": "y"}]),
            "testSuiteFileDuplicate",
        ),
        (
            _command(
                testFiles=[{"path": "big", "content": "x" * (2 * 1024 * 1024 + 1)}]
            ),
            "testSuiteFilesTooLarge",
        ),
    ],
)
def test_a_setter_is_told_what_is_wrong(entry, key):
    assert _refused(entry) == key


def test_scored_tests_carry_their_names():
    (line,) = suite_lines(
        [_script(cases=[{"testId": "tests.test_add::a", "points": 5}])]
    )
    (case,) = line.cases
    assert (case.test_id, case.name, case.points) == ("tests.test_add::a",) * 2 + (5,)


@pytest.mark.parametrize(
    ("parse", "given"),
    [
        ("parse_score_json", b'{"score": 1, "max": 1' + b"0" * 400 + b"}"),
        ("parse_score_line", "SCORE: 1/" + "9" * 400),
        ("parse_score_line", "SCORE: " + "9" * 400 + "/10"),
    ],
)
def test_a_score_too_big_to_be_a_score_is_malformed_on_the_judge_host(parse, given):
    """评测主机那一侧就把它判成坏的分数，不把它当一个数送回后端。"""
    from app.domain.judge import reports

    with pytest.raises(reports.ReportError):
        getattr(reports, parse)(given)
