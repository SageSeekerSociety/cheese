"""Lost runner results must fail even when every reported runner passed."""

import json
from pathlib import Path

import pytest

from scripts.assert_shards_complete import assert_complete
from scripts.assert_suite_ran import SuiteDidNotRun


def report(root: Path, name: str, cases: list[str], attempt: int = 1) -> Path:
    directory = root / f"{name}-{attempt}"
    directory.mkdir()
    (directory / "selection-main.json").write_text(
        json.dumps(
            {
                "shard_index": 0,
                "shard_count": 1,
                "selected": cases,
                "assigned": cases,
            }
        )
    )
    (directory / "results.xml").write_text(
        "<testsuite>"
        + "".join(
            f'<testcase name="{case}"><properties>'
            f'<property name="cheese_nodeid" value="{case}"/>'
            "</properties></testcase>"
            for case in cases
        )
        + "</testsuite>"
    )
    return directory


def test_complete_results_include_the_separate_search_suite(tmp_path):
    report(tmp_path, "plan", ["pure", "integration", "search"])
    report(tmp_path, "fast", ["pure"])
    report(tmp_path, "database", ["integration"])
    report(tmp_path, "search", ["search"])
    assert assert_complete(tmp_path, "plan") == 3


def test_an_entire_missing_runner_fails_even_with_all_received_reports_green(tmp_path):
    report(tmp_path, "plan", ["pure", "integration", "search"])
    report(tmp_path, "fast", ["pure"])
    report(tmp_path, "database", ["integration"])
    with pytest.raises(SuiteDidNotRun, match="1 missing.*search"):
        assert_complete(tmp_path, "plan")


def test_duplicate_runner_execution_is_not_coverage(tmp_path):
    report(tmp_path, "plan", ["same"])
    report(tmp_path, "first", ["same"])
    report(tmp_path, "second", ["same"])
    with pytest.raises(SuiteDidNotRun, match="multiple runners"):
        assert_complete(tmp_path, "plan")


def test_a_stale_report_cannot_replace_a_current_case(tmp_path):
    report(tmp_path, "plan", ["current"])
    report(tmp_path, "old", ["old"])
    with pytest.raises(SuiteDidNotRun, match="1 missing.*1 unexpected"):
        assert_complete(tmp_path, "plan")


def test_a_manifest_without_execution_is_not_coverage(tmp_path):
    report(tmp_path, "plan", ["case"])
    directory = report(tmp_path, "runner", ["case"])
    (directory / "results.xml").unlink()
    with pytest.raises(SuiteDidNotRun, match="does not exist"):
        assert_complete(tmp_path, "plan")


def test_failed_job_rerun_reuses_successes_and_preserves_previous_evidence(tmp_path):
    report(tmp_path, "plan", ["pure", "integration"])
    report(tmp_path, "fast", ["pure"])
    old = report(tmp_path, "database", ["wrong"])
    report(tmp_path, "database", ["integration"], attempt=2)
    assert assert_complete(tmp_path, "plan") == 2
    assert old.exists()
