"""The failing shard names its real reason, not just its exit code."""

import os
import re
import subprocess
import sys
from pathlib import Path

from scripts.report_failed_cases import (
    ANNOTATION_LIMIT,
    COMMAND_LIMIT,
    format_annotations,
    report,
)

BACKEND = Path(__file__).resolve().parents[2]

# Every command GitHub reads has the shape ``::error[ file=…,line=…]::text``.
_COMMAND = re.compile(r"^::error(?: file=[^,]+,line=\d+)?::\S")
_EMPTY_MESSAGE = "no failure message"


def case(
    name: str,
    *,
    file: str | None = None,
    line: str | None = None,
    tag: str = "failure",
    message: str = "AssertionError: boom",
    text: str = "",
    nodeid: str | None = None,
) -> str:
    attributes = f'classname="tests.unit.test_a" name="{name}"'
    if file is not None:
        attributes += f' file="{file}"'
    if line is not None:
        attributes += f' line="{line}"'
    properties = (
        f'<properties><property name="cheese_nodeid" value="{nodeid}"/></properties>'
        if nodeid
        else ""
    )
    return (
        f"<testcase {attributes}>{properties}"
        f'<{tag} message="{message}">{text}</{tag}></testcase>'
    )


def write_report(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "results.xml"
    path.write_text(
        f'<?xml version="1.0"?><testsuites><testsuite>{body}</testsuite></testsuites>'
    )
    return path


# A normal failure and an error in one file, three failures in another, and one
# case the report carries no location for at all.
FIXTURE = (
    case("test_normal", file="tests/unit/test_normal.py", line="12")
    + case(
        "test_errored",
        file="tests/unit/test_normal.py",
        line="30",
        tag="error",
        message="RuntimeError: fixture blew up",
    )
    + case(
        "test_first",
        file="tests/unit/test_many.py",
        line="5",
        message="AssertionError: a",
    )
    + case(
        "test_second",
        file="tests/unit/test_many.py",
        line="9",
        message="AssertionError: b",
    )
    + case(
        "test_third",
        file="tests/unit/test_many.py",
        line="14",
        message="AssertionError: c",
    )
    + case("test_unknown", message="ImportError: No module named 'missing'")
)


def failing_report(tmp_path: Path) -> Path:
    return write_report(tmp_path, FIXTURE)


def run_script(*arguments: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "scripts.report_failed_cases", *map(str, arguments)],
        cwd=BACKEND,
        capture_output=True,
        text=True,
        env=os.environ,
    )


def test_every_failing_file_gets_one_annotation_from_its_first_failure(
    tmp_path: Path,
) -> None:
    commands = report(failing_report(tmp_path), None)
    file_commands = [command for command in commands if " file=" in command]
    # One command per distinct file, most-failing first, each on the file's first
    # failure and carrying the file's count.
    assert file_commands[0].startswith(
        "::error file=backend/tests/unit/test_many.py,line=5::test_first"
    )
    assert "3 failures in this file" in file_commands[0]
    assert file_commands[1].startswith(
        "::error file=backend/tests/unit/test_normal.py,line=12::test_normal"
    )
    assert "2 failures in this file" in file_commands[1]


def test_a_case_without_a_line_is_a_plain_annotation(tmp_path: Path) -> None:
    commands = report(failing_report(tmp_path), None)
    plain = [command for command in commands if command.startswith("::error::")]
    assert any("test_unknown" in command for command in plain)
    assert any("ImportError" in command for command in plain)


def test_the_last_command_summarises_failures_and_files(tmp_path: Path) -> None:
    commands = report(failing_report(tmp_path), None)
    assert commands[-1] == "::error::6 failing case(s) across 3 file(s)"


def test_at_most_ten_error_commands_are_emitted(tmp_path: Path) -> None:
    # GitHub keeps ten error annotations per step; the rest are dropped silently.
    body = "".join(
        case(f"test_{index}", file=f"tests/unit/test_{index}.py", line=str(index))
        for index in range(15)
    )
    commands = report(write_report(tmp_path, body), None)
    assert len(commands) <= ANNOTATION_LIMIT
    assert all(command.startswith("::error") for command in commands)
    assert commands[-1].startswith("::error::15 failing case(s) across 15 file(s)")


def test_every_command_is_well_formed_and_within_the_size_limit(
    tmp_path: Path,
) -> None:
    for command in report(failing_report(tmp_path), None):
        assert _COMMAND.match(command), command
        assert len(command) <= COMMAND_LIMIT
        assert "\n" not in command


def test_a_long_message_is_truncated_to_fit(tmp_path: Path) -> None:
    body = case(
        "test_huge",
        file="tests/unit/test_huge.py",
        line="1",
        message="AssertionError: " + "x" * (COMMAND_LIMIT * 2),
    )
    (command,) = [
        c for c in report(write_report(tmp_path, body), None) if " file=" in c
    ]
    assert len(command) <= COMMAND_LIMIT
    assert command.endswith("…")


def test_a_location_is_derived_without_file_attributes(tmp_path: Path) -> None:
    # pytest's default xUnit 2 report carries no file/line; the failing frame of
    # the traceback and the shard's nodeid property are what remains.
    body = case(
        "test_sharded",
        message="AssertionError: boom",
        text="tests/unit/test_sharded.py:41: AssertionError\nassert 1 == 2",
        nodeid="tests/unit/test_sharded.py::test_sharded",
    )
    (command,) = [
        c for c in report(write_report(tmp_path, body), None) if " file=" in c
    ]
    assert command.startswith(
        "::error file=backend/tests/unit/test_sharded.py,line=41::"
    )


def test_a_missing_report_falls_back_to_the_pytest_log_tail(tmp_path: Path) -> None:
    log = tmp_path / "pytest.log"
    log.write_text(
        "\n".join(f"line {number}" for number in range(40)) + "\nlast real line\n"
    )
    commands = report(tmp_path / "missing.xml", log)
    assert len(commands) == 1
    assert commands[0].startswith("::error::")
    assert "last real line" in commands[0]
    assert "line 0" not in commands[0]


def test_no_report_and_no_log_says_the_shard_produced_nothing(tmp_path: Path) -> None:
    commands = report(tmp_path / "missing.xml", tmp_path / "missing.log")
    assert commands == [
        "::error::the shard produced no results (no results.xml, no pytest.log)"
    ]


def test_the_script_exits_zero_against_a_report(tmp_path: Path) -> None:
    result = run_script(failing_report(tmp_path))
    assert result.returncode == 0
    assert result.stdout.count("::error") >= 4


def test_the_script_exits_zero_with_neither_report_nor_log(tmp_path: Path) -> None:
    result = run_script(tmp_path / "missing.xml")
    assert result.returncode == 0
    assert "produced no results" in result.stdout


def test_the_script_exits_zero_against_a_corrupt_report(tmp_path: Path) -> None:
    path = tmp_path / "results.xml"
    path.write_text("<testsuites><testsuite>")
    assert run_script(path).returncode == 0


def test_format_annotations_keeps_nothing_when_there_are_no_failures() -> None:
    assert format_annotations([]) == ["::error::0 failing case(s) across 0 file(s)"]


def test_the_empty_message_placeholder_is_used() -> None:
    commands = format_annotations(
        [("backend/tests/unit/x.py", "3", "test_x", _EMPTY_MESSAGE)]
    )
    assert _EMPTY_MESSAGE in commands[0]
