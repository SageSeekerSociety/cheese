"""The gate every suite-running job ends with: did the suite actually run?"""

from pathlib import Path

import pytest

from scripts.assert_suite_ran import SuiteDidNotRun, assert_suite_ran


def report(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "results.xml"
    path.write_text(
        '<?xml version="1.0"?>'
        f"<testsuites><testsuite>{body}</testsuite></testsuites>"
    )
    return path


PASSED = '<testcase classname="tests.unit.test_a" name="test_one"/>'
FAILED = (
    '<testcase classname="tests.unit.test_a" name="test_two">'
    '<failure message="nope"/></testcase>'
)
SKIPPED = (
    '<testcase classname="tests.unit.test_a" name="test_three">'
    '<skipped message="Codex binary required"/></testcase>'
)
ERRORED = (
    '<testcase classname="tests.unit.test_a" name="test_four">'
    '<error message="fixture blew up"/></testcase>'
)


def test_a_full_run_passes_the_gate(tmp_path: Path) -> None:
    assert assert_suite_ran(report(tmp_path, PASSED * 3), at_least=3) == 3


def test_a_failed_case_still_counts_as_having_run(tmp_path: Path) -> None:
    # A red case told us something; the gate is about silence, not about green.
    assert assert_suite_ran(report(tmp_path, PASSED + FAILED), at_least=2) == 2


def test_one_skipped_case_fails_the_gate(tmp_path: Path) -> None:
    with pytest.raises(SuiteDidNotRun) as raised:
        assert_suite_ran(report(tmp_path, PASSED * 5 + SKIPPED), at_least=1)
    assert "test_three" in str(raised.value)
    assert "Codex binary required" in str(raised.value)


def test_a_case_that_errored_in_setup_did_not_run(tmp_path: Path) -> None:
    with pytest.raises(SuiteDidNotRun) as raised:
        assert_suite_ran(report(tmp_path, PASSED + ERRORED), at_least=2)
    assert "only 1" in str(raised.value)


def test_a_collapsed_selection_fails_even_though_every_case_passed(
    tmp_path: Path,
) -> None:
    # #1236: the canary was green for two days on one test out of eight.
    with pytest.raises(SuiteDidNotRun) as raised:
        assert_suite_ran(report(tmp_path, PASSED), at_least=8)
    assert "at least 8" in str(raised.value)


def test_a_report_that_was_never_written_fails_the_gate(tmp_path: Path) -> None:
    with pytest.raises(SuiteDidNotRun) as raised:
        assert_suite_ran(tmp_path / "missing.xml", at_least=1)
    assert "does not exist" in str(raised.value)


def test_an_unreadable_report_fails_the_gate(tmp_path: Path) -> None:
    path = tmp_path / "results.xml"
    path.write_text("<testsuites><testsuite>")
    with pytest.raises(SuiteDidNotRun):
        assert_suite_ran(path, at_least=1)
