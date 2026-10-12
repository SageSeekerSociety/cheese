"""The failing shard names its real reason, not just its exit code."""

import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

from scripts.report_failed_cases import (
    ANNOTATION_LIMIT,
    COMMAND_LIMIT,
    Failure,
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
        [Failure("backend/tests/unit/x.py", "3", None, "test_x", _EMPTY_MESSAGE)]
    )
    assert _EMPTY_MESSAGE in commands[0]


def _rerun_in(annotation: str) -> str:
    """The rerun command an annotation carries, without the ``rerun: `` label."""
    return annotation.split("rerun: ", 1)[1]


def _argument_after_pytest(annotation: str) -> str:
    """The text the annotation hands to ``pytest``, quoting and all."""
    return _rerun_in(annotation).split("pytest ", 1)[1]


def _shell_arguments(text: str) -> list[str]:
    """The argv a real shell builds from ``text``.

    The property under test is where the shell decides one argument ends, so a
    shell has to answer it; ``shlex.split`` shares the bug it would be checking
    for. ``%s\\0`` keeps the arguments separable, and ``check`` turns bash's own
    complaint — an unterminated quote — into a failure here.
    """
    done = subprocess.run(
        ["bash", "-c", f'printf "%s\\0" {text}'],
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout.split("\0")[:-1]


# Node ids this suite really produces. A parametrised id quotes the tested text,
# so an id can carry an apostrophe, a double quote, a pipe or a space — quoting
# the id with a fixed pair of single quotes ends early on the first id, and a
# pair of double quotes on the second.
REAL_NODE_IDS = [
    "tests/unit/test_machine_files.py::test_find_matches_names_and_paths_and_"
    "skips_what_git_ignores[the platform's ripgrep]",
    "tests/unit/test_analytics_view_helpers.py::TestCsvRow::"
    'test_a_cell_that_would_be_a_formula_is_neutralised[=HYPERLINK("http://'
    'evil.example","click")]',
    "tests/unit/test_analytics_view_helpers.py::TestCsvRow::"
    "test_a_cell_that_would_be_a_formula_is_neutralised[=cmd|'/C calc'!A1]",
]


def test_an_annotation_names_the_case_by_node_id_and_how_to_rerun_it(
    tmp_path: Path,
) -> None:
    # The node id is what a reader pastes into pytest; the bare name does not say
    # which module's ``test_fails`` broke. The parametrised id has a space and
    # brackets in it, so the shell only reaches pytest with it intact if it is
    # quoted — the argv below is the property, not the quoting style.
    nodeid = "tests/unit/test_mod.py::TestWidget::test_import_error[case 2]"
    body = case(
        "test_import_error[case 2]",
        file="tests/unit/test_mod.py",
        line="18",
        message="AssertionError: boom",
        nodeid=nodeid,
    )
    (command,) = [
        c for c in report(write_report(tmp_path, body), None) if " file=" in c
    ]
    assert command.startswith(
        "::error file=backend/tests/unit/test_mod.py,line=18::"
        "tests/unit/test_mod.py::TestWidget::test_import_error[case 2] —"
    )
    assert shlex.split(_rerun_in(command)) == [
        "cd",
        "backend",
        "&&",
        "uv",
        "run",
        "pytest",
        nodeid,
    ]


def test_every_real_node_id_reaches_the_shell_as_one_argument() -> None:
    # ``shlex.split`` proves the annotation and the test agree with each other;
    # only a shell proves the command reaches pytest, which is what the reader
    # pastes into a terminal.
    for nodeid in REAL_NODE_IDS:
        commands = format_annotations(
            [Failure("backend/tests/unit/x.py", "3", nodeid, "test_x", "boom")]
        )
        assert _shell_arguments(_argument_after_pytest(commands[0])) == [nodeid], nodeid


def test_without_a_node_id_the_name_names_the_case_and_no_rerun_is_offered(
    tmp_path: Path,
) -> None:
    # pytest's xUnit 1 report carries file/line but no node id; the annotation
    # then falls back to the test name and offers no command, rather than
    # inventing one that will not resolve.
    body = case("test_named_only", file="tests/unit/test_mod.py", line="7")
    (command,) = [
        c for c in report(write_report(tmp_path, body), None) if " file=" in c
    ]
    assert "test_named_only —" in command
    assert "rerun:" not in command
