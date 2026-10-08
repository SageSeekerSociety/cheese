"""The gate every suite-running job ends with: did the suite actually run?"""

import hashlib
import json
from pathlib import Path

import pytest

from scripts.assert_suite_ran import SuiteDidNotRun, assert_suite_ran, not_clean


def report(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "results.xml"
    path.write_text(
        f'<?xml version="1.0"?><testsuites><testsuite>{body}</testsuite></testsuites>'
    )
    return path


def selection(
    tmp_path: Path, selected: list[str], *, index: int = 0, count: int = 1
) -> Path:
    directory = tmp_path / "selection"
    directory.mkdir()
    assigned = [
        nodeid
        for nodeid in selected
        if int.from_bytes(hashlib.sha256(nodeid.encode()).digest()) % count == index
    ]
    (directory / "selection-main.json").write_text(
        json.dumps(
            {
                "shard_index": index,
                "shard_count": count,
                "markexpr": "pure",
                "keyword": "not kotlin",
                "selected": selected,
                "assigned": assigned,
            }
        )
    )
    return directory


def passed_nodeid(nodeid: str) -> str:
    return (
        '<testcase classname="tests.unit.test_a" name="test_one">'
        '<properties><property name="cheese_nodeid" '
        f'value="{nodeid}"/></properties></testcase>'
    )


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
    assert "fixture blew up" in str(raised.value)


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


def test_a_shard_runs_every_case_its_manifest_assigned(tmp_path: Path) -> None:
    selected = ["tests/unit/test_a.py::test_one", "tests/unit/test_a.py::test_two"]
    manifests = selection(tmp_path, selected)
    assert (
        assert_suite_ran(
            report(tmp_path, "".join(passed_nodeid(nodeid) for nodeid in selected)),
            at_least=2,
            selection_dir=manifests,
        )
        == 2
    )


def test_a_shard_with_a_missing_junit_case_fails(tmp_path: Path) -> None:
    selected = ["tests/unit/test_a.py::test_one", "tests/unit/test_a.py::test_two"]
    manifests = selection(tmp_path, selected)
    with pytest.raises(SuiteDidNotRun, match="manifest assigned 2"):
        assert_suite_ran(
            report(tmp_path, passed_nodeid(selected[0])),
            at_least=2,
            selection_dir=manifests,
        )


def test_a_collapsed_full_layer_manifest_fails_the_floor(tmp_path: Path) -> None:
    nodeid = "tests/unit/test_a.py::test_one"
    manifests = selection(tmp_path, [nodeid])
    with pytest.raises(SuiteDidNotRun, match="at least 8"):
        assert_suite_ran(
            report(tmp_path, passed_nodeid(nodeid)),
            at_least=8,
            selection_dir=manifests,
        )


def test_missing_selection_manifests_fail_closed(tmp_path: Path) -> None:
    directory = tmp_path / "selection"
    directory.mkdir()
    with pytest.raises(SuiteDidNotRun, match="no selection manifests"):
        assert_suite_ran(report(tmp_path, PASSED), at_least=1, selection_dir=directory)


def test_worker_manifests_must_agree(tmp_path: Path) -> None:
    selected = ["tests/unit/test_a.py::test_one"]
    directory = selection(tmp_path, selected)
    manifest = json.loads((directory / "selection-main.json").read_text())
    manifest["keyword"] = "different"
    (directory / "selection-gw0.json").write_text(json.dumps(manifest))
    with pytest.raises(SuiteDidNotRun, match="disagrees"):
        assert_suite_ran(report(tmp_path, PASSED), at_least=1, selection_dir=directory)


def test_manifest_rejects_duplicate_nodeids(tmp_path: Path) -> None:
    directory = selection(tmp_path, ["tests/unit/test_a.py::test_one"])
    path = directory / "selection-main.json"
    manifest = json.loads(path.read_text())
    manifest["selected"] *= 2
    path.write_text(json.dumps(manifest))
    with pytest.raises(SuiteDidNotRun, match="unique"):
        assert_suite_ran(report(tmp_path, PASSED), at_least=1, selection_dir=directory)


def test_manifest_rejects_the_wrong_partition(tmp_path: Path) -> None:
    selected = [f"tests/unit/test_a.py::test_{number}" for number in range(10)]
    directory = selection(tmp_path, selected, index=0, count=2)
    path = directory / "selection-main.json"
    manifest = json.loads(path.read_text())
    manifest["assigned"] = selected
    path.write_text(json.dumps(manifest))
    with pytest.raises(SuiteDidNotRun, match="do not match shard"):
        assert_suite_ran(
            report(tmp_path, PASSED * 10), at_least=2, selection_dir=directory
        )


def test_equal_size_wrong_junit_identity_fails(tmp_path: Path) -> None:
    selected = ["tests/unit/test_a.py::test_one"]
    directory = selection(tmp_path, selected)
    wrong = passed_nodeid("tests/unit/test_a.py::test_other")
    with pytest.raises(SuiteDidNotRun, match="unexpected"):
        assert_suite_ran(report(tmp_path, wrong), at_least=1, selection_dir=directory)


def test_duplicate_junit_identity_fails(tmp_path: Path) -> None:
    selected = ["tests/unit/test_a.py::test_one", "tests/unit/test_a.py::test_two"]
    directory = selection(tmp_path, selected)
    repeated = passed_nodeid(selected[0]) * 2
    with pytest.raises(SuiteDidNotRun, match="duplicate cheese_nodeid"):
        assert_suite_ran(
            report(tmp_path, repeated), at_least=2, selection_dir=directory
        )


# What pytest's JUnit writer leaves for the report xdist sends when a worker
# dies mid-case (recorded from a real run); the retry reports the case again.
CRASH_PLACEHOLDER = '<testcase time="0.000"/>'
QUARANTINED = (
    '<testcase classname="tests.unit.test_a" name="test_five">'
    '<skipped type="pytest.xfail" '
    'message="quarantined: https://github.com/o/r/issues/1"/></testcase>'
)


def reruns(tmp_path: Path, *records: dict) -> Path:
    path = tmp_path / "reruns.jsonl"
    path.write_text("".join(json.dumps(record) + "\n" for record in records))
    return path


def test_a_crash_placeholder_needs_a_recorded_crash(tmp_path: Path) -> None:
    results = report(tmp_path, PASSED + CRASH_PLACEHOLDER)
    with pytest.raises(SuiteDidNotRun, match="nameless"):
        assert_suite_ran(results, at_least=1)
    retried = reruns(
        tmp_path, {"nodeid": "tests/unit/test_a.py::test_one", "crashed": True}
    )
    assert assert_suite_ran(results, at_least=1, reruns=retried) == 1


def test_a_failed_retry_does_not_explain_a_placeholder(tmp_path: Path) -> None:
    retried = reruns(
        tmp_path, {"nodeid": "tests/unit/test_a.py::test_one", "crashed": False}
    )
    with pytest.raises(SuiteDidNotRun, match="0 recorded worker crash"):
        assert_suite_ran(
            report(tmp_path, PASSED + CRASH_PLACEHOLDER), at_least=1, reruns=retried
        )


def test_a_quarantined_case_ran_and_is_named(tmp_path: Path, monkeypatch) -> None:
    nodeid = "tests/unit/test_a.py::test_five"
    listing = tmp_path / "quarantine.txt"
    listing.write_text(f"{nodeid} # https://github.com/o/r/issues/1\n")
    monkeypatch.setattr("scripts.assert_suite_ran._QUARANTINE", listing)
    case = QUARANTINED.replace(
        "<skipped",
        '<properties><property name="cheese_nodeid" '
        f'value="{nodeid}"/></properties><skipped',
    )
    results = report(tmp_path, PASSED + case)
    assert assert_suite_ran(results, at_least=2) == 2
    [line] = not_clean(results, None)
    assert "test_five is quarantined" in line


@pytest.mark.parametrize(
    "identity", ["unlisted", "wrong_issue", "missing", "duplicate"]
)
def test_a_quarantine_reason_alone_does_not_authorize_a_skip(
    tmp_path: Path, identity: str, monkeypatch
) -> None:
    nodeid = "tests/unit/test_a.py::test_five"
    listing = tmp_path / "quarantine.txt"
    issue = (
        "https://github.com/o/r/issues/2"
        if identity == "wrong_issue"
        else "https://github.com/o/r/issues/1"
    )
    listing.write_text("" if identity == "unlisted" else f"{nodeid} # {issue}\n")
    monkeypatch.setattr("scripts.assert_suite_ran._QUARANTINE", listing)
    case = QUARANTINED
    if identity != "missing":
        prop = f'<property name="cheese_nodeid" value="{nodeid}"/>'
        props = prop * (2 if identity == "duplicate" else 1)
        case = case.replace("<skipped", f"<properties>{props}</properties><skipped")
    results = report(tmp_path, PASSED + case)
    with pytest.raises(SuiteDidNotRun, match="skipped"):
        assert_suite_ran(results, at_least=2)


def test_an_ordinary_xfail_is_still_a_skip(tmp_path: Path) -> None:
    xfail = QUARANTINED.replace("quarantined: ", "")
    with pytest.raises(SuiteDidNotRun, match="skipped"):
        assert_suite_ran(report(tmp_path, PASSED + xfail), at_least=1)


def test_only_a_retry_that_passed_is_named(tmp_path: Path) -> None:
    one, two = "tests/unit/test_a.py::test_one", "tests/unit/test_a.py::test_two"
    failed_two = (
        '<testcase classname="tests.unit.test_a" name="test_two">'
        f'<properties><property name="cheese_nodeid" value="{two}"/></properties>'
        '<failure message="nope"/></testcase>'
    )
    retried = reruns(
        tmp_path,
        {"nodeid": one, "crashed": False, "reason": "flaky"},
        {"nodeid": two, "crashed": False, "reason": "broken"},
    )
    lines = not_clean(report(tmp_path, passed_nodeid(one) + failed_two), retried)
    assert lines == [f"{one} passed only on a retry; first it failed: flaky"]


@pytest.mark.parametrize("phase,copies", [("call", 2), ("setup", 2), ("teardown", 3)])
def test_retries_do_not_explain_unrelated_duplicate_results(
    tmp_path: Path, phase: str, copies: int
) -> None:
    nodeid = "tests/unit/test_a.py::test_one"
    directory = selection(tmp_path, [nodeid])
    retried = reruns(tmp_path, {"nodeid": nodeid, "when": phase})
    with pytest.raises(SuiteDidNotRun):
        assert_suite_ran(
            report(tmp_path, passed_nodeid(nodeid) * copies),
            at_least=1,
            selection_dir=directory,
            reruns=retried,
        )


@pytest.mark.parametrize("outcome", ["failure", "error", "skipped"])
def test_a_teardown_retry_never_hides_a_bad_result(
    tmp_path: Path, outcome: str
) -> None:
    nodeid = "tests/unit/test_a.py::test_one"
    directory = selection(tmp_path, [nodeid])
    retried = reruns(tmp_path, {"nodeid": nodeid, "when": "teardown"})
    bad = passed_nodeid(nodeid).replace("</testcase>", f"<{outcome}/></testcase>")
    results = report(tmp_path, bad + passed_nodeid(nodeid))
    with pytest.raises(SuiteDidNotRun):
        assert_suite_ran(results, at_least=1, selection_dir=directory, reruns=retried)
